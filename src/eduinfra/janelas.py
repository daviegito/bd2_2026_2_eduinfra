"""Eventos de vistoria na camada analítica, em janelas mensais, com dado atrasado tratado.

Decisões (issue #27; detalhes em docs/dado-atrasado.md):

- A janela é o mês de ``ocorrido_em``, quando a realidade mudou na escola, e não
  o de ``registrado_em``, quando o sistema soube. Uma vistoria registrada com
  atraso cai no mês em que aconteceu.
- O que ainda não foi processado é descoberto pela marca d'água sobre
  ``registrado_em``: cada execução pega as vistorias registradas depois da
  última e reprocessa as janelas dos meses em que elas ocorreram.
- Reprocessar uma janela é regravar a partição inteira do mês a partir da
  origem, que é insert-only e guarda tudo. Por isso é idempotente.
- Uma janela fica aberta até TOLERANCIA depois do fim do mês. Um evento que
  chega depois disso não é descartado: a janela é reaberta, corrigida e a
  reabertura fica registrada no controle, para quem já usou os números do mês.
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import psycopg
from psycopg import sql

from eduinfra.configuracao import Configuracao

log = logging.getLogger(__name__)

TOLERANCIA = timedelta(days=7)

# O mesmo codec da camada Silver, escolhido por medição (issue #28).
COMPRESSAO_PARQUET = "zstd"

# Um registro com registrado_em pouco anterior à última marca pode ter sido
# confirmado depois dela: now() é a hora de início da transação, não a do commit.
# Reprocessar essa margem de novo é inofensivo, porque a regravação é idempotente.
MARGEM_DA_MARCA = timedelta(minutes=10)

COLUNAS = {
    "vistoria_id": "BIGINT",
    "co_entidade": "INTEGER",
    "origem": "VARCHAR",
    "ocorrido_em": "TIMESTAMPTZ",
    "registrado_em": "TIMESTAMPTZ",
    "item_codigo": "VARCHAR",
    "situacao": "VARCHAR",
    "quantidade": "INTEGER",
}

EVENTOS_DA_JANELA = """
select v.id as vistoria_id, v.co_entidade, v.origem, v.ocorrido_em, v.registrado_em,
       i.item_codigo, i.situacao::text as situacao, i.quantidade
from vistoria v
join vistoria_item i on i.vistoria_id = v.id
where v.ocorrido_em >= {inicio} and v.ocorrido_em < {fim}
order by v.id, i.item_codigo
"""


@dataclass(frozen=True)
class Janela:
    mes: str
    linhas: int
    impressao: str


@dataclass(frozen=True)
class EventoNovo:
    vistoria_id: int
    mes: str
    ocorrido_em: datetime
    registrado_em: datetime

    @property
    def atraso(self) -> timedelta:
        return self.registrado_em - self.ocorrido_em

    @property
    def dentro_da_tolerancia(self) -> bool:
        return self.registrado_em <= fechamento(self.mes)


@dataclass
class Relatorio:
    marca_anterior: datetime | None
    marca_nova: datetime | None
    eventos: list[EventoNovo] = field(default_factory=list)
    janelas: list[Janela] = field(default_factory=list)


def mes_de(instante: datetime) -> str:
    return instante.astimezone(timezone.utc).strftime("%Y-%m")


def limites(mes: str) -> tuple[datetime, datetime]:
    ano, numero = (int(parte) for parte in mes.split("-"))
    inicio = datetime(ano, numero, 1, tzinfo=timezone.utc)
    fim = datetime(ano + numero // 12, numero % 12 + 1, 1, tzinfo=timezone.utc)

    return inicio, fim


def fechamento(mes: str) -> datetime:
    return limites(mes)[1] + TOLERANCIA


def pasta_eventos(configuracao: Configuracao) -> Path:
    return configuracao.diretorio_dados / "analitico" / "eventos_vistoria"


def processar(configuracao: Configuracao) -> Relatorio:
    """Reprocessa as janelas tocadas por vistorias registradas desde a última execução."""
    base = pasta_eventos(configuracao)
    controle = _ler_controle(base)
    marca = datetime.fromisoformat(controle["marca_dagua"]) if controle.get("marca_dagua") else None

    with psycopg.connect(configuracao.dsn) as conexao:
        # O limite vem primeiro: o que for registrado durante esta execução fica
        # para a próxima, e a marca nunca passa à frente do que foi lido.
        limite = conexao.execute("select max(registrado_em) from vistoria").fetchone()[0]
        if limite is None:
            return Relatorio(marca_anterior=marca, marca_nova=marca)

        piso = marca - MARGEM_DA_MARCA if marca else datetime.min.replace(tzinfo=timezone.utc)
        candidatos = [
            EventoNovo(vistoria_id=id_, mes=mes_de(ocorrido), ocorrido_em=ocorrido, registrado_em=registrado)
            for id_, ocorrido, registrado in conexao.execute(
                """
                select id, ocorrido_em, registrado_em from vistoria
                where registrado_em > %s and registrado_em <= %s
                order by id
                """,
                (piso, limite),
            ).fetchall()
        ]

        # A margem traz de volta eventos já incorporados; só é novo o que ainda
        # não está na partição do seu mês. Assim a margem não regrava janelas à toa.
        incorporados = {mes: _ids_na_particao(base, mes) for mes in {e.mes for e in candidatos}}
        eventos = [e for e in candidatos if e.vistoria_id not in incorporados[e.mes]]

        janelas = [_regravar(conexao, base, mes) for mes in sorted({e.mes for e in eventos})]

    # Na primeira execução tudo é carga inicial, não atraso.
    reabertas = [e for e in eventos if marca and not e.dentro_da_tolerancia]
    for mes in sorted({e.mes for e in reabertas}):
        do_mes = [e for e in reabertas if e.mes == mes]
        controle.setdefault("reaberturas", []).append(
            {
                "mes": mes,
                "reaberta_em": limite.isoformat(),
                "eventos": len(do_mes),
                "maior_atraso_dias": round(max(e.atraso for e in do_mes).total_seconds() / 86400, 1),
            }
        )
        log.warning(
            "janela %s, fechada desde %s, reaberta por %d vistoria(s) registrada(s) com atraso",
            mes, fechamento(mes).date(), len(do_mes),
        )

    controle["marca_dagua"] = limite.isoformat()
    for janela in janelas:
        controle.setdefault("janelas", {})[janela.mes] = asdict(janela)
    _gravar_controle(base, controle)

    return Relatorio(marca_anterior=marca, marca_nova=limite, eventos=eventos, janelas=janelas)


def _ids_na_particao(base: Path, mes: str) -> set[int]:
    particao = base / f"ocorrido_mes={mes}" / "eventos.parquet"
    if not particao.exists():
        return set()

    leitor = duckdb.connect()
    try:
        linhas = leitor.execute(
            f"select distinct vistoria_id from read_parquet('{particao.as_posix()}')"
        ).fetchall()
    finally:
        leitor.close()

    return {linha[0] for linha in linhas}


def reprocessar_janela(configuracao: Configuracao, mes: str) -> Janela:
    """Regrava a janela de um mês a partir da origem. Rodar duas vezes dá o mesmo resultado."""
    limites(mes)  # valida o formato AAAA-MM antes de abrir conexão
    base = pasta_eventos(configuracao)

    with psycopg.connect(configuracao.dsn) as conexao:
        janela = _regravar(conexao, base, mes)

    controle = _ler_controle(base)
    controle.setdefault("janelas", {})[mes] = asdict(janela)
    _gravar_controle(base, controle)

    return janela


def ler_janelas(configuracao: Configuracao) -> dict[str, Janela]:
    controle = _ler_controle(pasta_eventos(configuracao))
    return {mes: Janela(**dados) for mes, dados in sorted(controle.get("janelas", {}).items())}


def _regravar(conexao: psycopg.Connection, base: Path, mes: str) -> Janela:
    inicio, fim = limites(mes)
    particao = base / f"ocorrido_mes={mes}"
    destino = particao / "eventos.parquet"
    base.mkdir(parents=True, exist_ok=True)

    consulta = sql.SQL(EVENTOS_DA_JANELA).format(inicio=sql.Literal(inicio), fim=sql.Literal(fim))
    with tempfile.TemporaryDirectory(dir=base, prefix="_tmp_") as temporaria:
        csv = Path(temporaria) / "eventos.csv"
        with csv.open("wb") as saida:
            with conexao.cursor().copy(
                sql.SQL("copy ({}) to stdout with (format csv, header true)").format(consulta)
            ) as copia:
                for bloco in copia:
                    saida.write(bloco)

        janela = gravar_particao(csv, destino, mes, Path(temporaria))

    log.info("janela %s regravada: %d linhas", mes, janela.linhas)
    return janela


def gravar_particao(csv: Path, destino: Path, mes: str, temporaria: Path) -> Janela:
    """Converte o CSV extraído da origem em Parquet e troca a partição de uma vez.

    A troca é um os.replace no mesmo sistema de arquivos: quem lê vê a partição
    antiga ou a nova, nunca uma escrita pela metade. Janela sem eventos remove a
    partição, para que a camada analítica nunca tenha mais do que a origem.
    """
    colunas = ", ".join(f"'{nome}': '{tipo}'" for nome, tipo in COLUNAS.items())
    leitor = duckdb.connect()
    try:
        # A janela da carga do Censo tem 3 milhões de linhas: limite de memória e
        # transbordo em disco para não depender da RAM livre da máquina.
        leitor.execute(f"set memory_limit = '1GB'; set threads = 2; set temp_directory = '{temporaria.as_posix()}'")
        leitor.execute("set timezone = 'UTC'")
        leitor.execute(
            f"create view eventos as select * from read_csv('{csv.as_posix()}', header = true, columns = {{{colunas}}})"
        )
        linhas = leitor.execute("select count(*) from eventos").fetchone()[0]

        if linhas == 0:
            if destino.exists():
                destino.unlink()
                destino.parent.rmdir()
            return Janela(mes=mes, linhas=0, impressao="0")

        novo = temporaria / "eventos.parquet"
        leitor.execute(
            f"copy (select * from eventos order by vistoria_id, item_codigo) "
            f"to '{novo.as_posix()}' (format parquet, compression {COMPRESSAO_PARQUET})"
        )
        impressao = _impressao(leitor, novo)
    finally:
        leitor.close()

    destino.parent.mkdir(parents=True, exist_ok=True)
    os.replace(novo, destino)

    return Janela(mes=mes, linhas=linhas, impressao=impressao)


def impressao_da_particao(destino: Path) -> str:
    leitor = duckdb.connect()
    try:
        return _impressao(leitor, destino)
    finally:
        leitor.close()


def _impressao(leitor: duckdb.DuckDBPyConnection, parquet: Path) -> str:
    # Soma dos hashes de cada linha: não depende da ordem física no arquivo e
    # muda se qualquer valor de qualquer linha mudar.
    colunas = ", ".join(COLUNAS)
    total = leitor.execute(
        f"select coalesce(sum(hash({colunas})), 0) from read_parquet('{parquet.as_posix()}')"
    ).fetchone()[0]

    return format(int(total), "x")


def _ler_controle(base: Path) -> dict:
    arquivo = base / "_controle.json"
    if not arquivo.exists():
        return {}

    return json.loads(arquivo.read_text(encoding="utf-8"))


def _gravar_controle(base: Path, controle: dict) -> None:
    base.mkdir(parents=True, exist_ok=True)
    temporario = base / "_controle.json.tmp"
    temporario.write_text(json.dumps(controle, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporario, base / "_controle.json")


def resumo_texto(relatorio: Relatorio) -> str:
    linhas = [
        f"marca d'água: {relatorio.marca_anterior.isoformat() if relatorio.marca_anterior else 'nenhuma (primeira execução)'}"
        f" -> {relatorio.marca_nova.isoformat() if relatorio.marca_nova else 'nenhuma'}",
        f"vistorias novas desde a marca: {len(relatorio.eventos)}",
    ]
    for janela in relatorio.janelas:
        linhas.append(f"  janela {janela.mes}: {janela.linhas} linhas, impressão {janela.impressao}")
    if relatorio.marca_anterior:
        for evento in relatorio.eventos[:10]:
            situacao = "dentro da tolerância" if evento.dentro_da_tolerancia else "FORA da tolerância: janela reaberta"
            linhas.append(
                f"  vistoria {evento.vistoria_id}: ocorreu {evento.ocorrido_em:%Y-%m-%d}, registrada "
                f"{evento.registrado_em:%Y-%m-%d}, atraso {evento.atraso.total_seconds() / 86400:.1f} dias "
                f"-> janela {evento.mes} ({situacao})"
            )
        if len(relatorio.eventos) > 10:
            linhas.append(f"  ... e mais {len(relatorio.eventos) - 10} vistorias")

    return "\n".join(linhas)

