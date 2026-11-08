"""Janelas mensais de dado atrasado sobre a fonte canônica de eventos (ADR 0008).

Decisões (issue #27; detalhes em docs/dado-atrasado.md):

- A janela é o mês de ``ocorrido_em``, quando a realidade mudou na escola, e não
  o de ``registrado_em``, quando o sistema soube. É a partição da tabela Delta
  ``warehouse/eventos_vistoria``: um evento atrasado cai no mês em que aconteceu,
  venha pelo CDC ou por aqui.
- As janelas não têm destino próprio: completam a fonte canônica com o mesmo
  merge do CDC, pela mesma chave (vistoria_id, item_codigo). O que o CDC já
  entregou não é gravado de novo.
- O que é novo é descoberto pela marca d'água sobre ``registrado_em``: cada
  execução pega as vistorias registradas depois da última e reconcilia as janelas
  dos meses em que elas ocorreram.
- Reconciliar uma janela compara a impressão do mês na origem com a do mês na
  fonte canônica e grava o que falta. A origem é insert-only e guarda tudo, então
  repetir dá o mesmo resultado.
- Uma janela fica aberta até TOLERANCIA depois do fim do mês. Um evento que
  chega depois disso não é descartado: a janela é reaberta e a reabertura fica
  registrada no controle, para quem já usou os números do mês.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

from eduinfra import eventos as fonte
from eduinfra.configuracao import Configuracao

log = logging.getLogger(__name__)

TOLERANCIA = timedelta(days=7)

# Um registro com registrado_em pouco anterior à última marca pode ter sido
# confirmado depois dela: now() é a hora de início da transação, não a do commit.
# Cada execução relê essa margem e só trata como novo o que a anterior não viu.
MARGEM_DA_MARCA = timedelta(minutes=10)


@dataclass(frozen=True)
class Janela:
    mes: str
    linhas: int
    impressao: str
    confere_com_a_origem: bool


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
    completados: int = 0


def mes_de(instante: datetime) -> str:
    return instante.astimezone(timezone.utc).strftime("%Y-%m")


def limites(mes: str) -> tuple[datetime, datetime]:
    ano, numero = (int(parte) for parte in mes.split("-"))
    inicio = datetime(ano, numero, 1, tzinfo=timezone.utc)
    fim = datetime(ano + numero // 12, numero % 12 + 1, 1, tzinfo=timezone.utc)

    return inicio, fim


def fechamento(mes: str) -> datetime:
    return limites(mes)[1] + TOLERANCIA


def arquivo_de_controle(configuracao: Configuracao) -> Path:
    # Ao lado da tabela, e não dentro: o diretório de uma tabela Delta é do log dela.
    return fonte.caminho_da_tabela(configuracao).with_name(f"{fonte.NOME_DA_TABELA}_janelas.json")


def processar(configuracao: Configuracao) -> Relatorio:
    """Reconcilia as janelas tocadas por vistorias registradas desde a última execução."""
    controle = _ler_controle(configuracao)
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

    # A margem traz de volta vistorias que a execução anterior já contou. Não dá para
    # perguntar à fonte canônica, porque o CDC pode ter entregado antes sem que nenhuma
    # execução das janelas as visse: a anterior guarda quais viu na margem.
    vistas = _expandir(controle.get("vistas_na_margem", []))
    eventos = [e for e in candidatos if not (marca and e.registrado_em <= marca and e.vistoria_id in vistas)]

    reconciliadas = [_reconciliar(configuracao, mes) for mes in sorted({e.mes for e in eventos})]
    janelas = [janela for janela, _ in reconciliadas]

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
    controle["vistas_na_margem"] = _faixas(e.vistoria_id for e in candidatos if e.registrado_em > limite - MARGEM_DA_MARCA)
    for janela in janelas:
        controle.setdefault("janelas", {})[janela.mes] = asdict(janela)
    _gravar_controle(configuracao, controle)

    return Relatorio(
        marca_anterior=marca,
        marca_nova=limite,
        eventos=eventos,
        janelas=janelas,
        completados=sum(inseridos for _, inseridos in reconciliadas),
    )


def reprocessar_janela(configuracao: Configuracao, mes: str) -> Janela:
    """Reconcilia a janela de um mês com a origem. Rodar duas vezes dá o mesmo resultado."""
    limites(mes)  # valida o formato AAAA-MM antes de abrir conexão
    janela, _ = _reconciliar(configuracao, mes)

    controle = _ler_controle(configuracao)
    controle.setdefault("janelas", {})[mes] = asdict(janela)
    _gravar_controle(configuracao, controle)

    return janela


def ler_janelas(configuracao: Configuracao) -> dict[str, Janela]:
    controle = _ler_controle(configuracao)
    return {mes: Janela(**dados) for mes, dados in sorted(controle.get("janelas", {}).items())}


def _reconciliar(configuracao: Configuracao, mes: str) -> tuple[Janela, int]:
    """Grava na fonte canônica o que a origem tem do mês e ela ainda não. Devolve a janela e quantos faltavam."""
    inicio, fim = limites(mes)
    na_origem = fonte.ler_da_origem(
        configuracao,
        f"v.ocorrido_em >= '{inicio.isoformat()}'::timestamptz and v.ocorrido_em < '{fim.isoformat()}'::timestamptz",
    )
    impressao_da_origem = fonte.impressao(na_origem)

    na_fonte, _ = fonte.ler(configuracao, mes)
    inseridos = 0
    # Mês igual à origem não ganha merge: além de caro, cada merge é uma versão nova da tabela.
    if fonte.impressao(na_fonte) != impressao_da_origem:
        inseridos = fonte.gravar_sem_duplicar(configuracao, na_origem).inseridas
        na_fonte, _ = fonte.ler(configuracao, mes)

    impressao = fonte.impressao(na_fonte)
    janela = Janela(mes=mes, linhas=na_fonte.num_rows, impressao=impressao, confere_com_a_origem=impressao == impressao_da_origem)
    if not janela.confere_com_a_origem:
        log.error("janela %s não confere com a origem depois de reconciliada: %d linhas na fonte, %d na origem",
                  mes, na_fonte.num_rows, na_origem.num_rows)
    log.info("janela %s reconciliada: %d linhas, %d completadas pelas janelas", mes, janela.linhas, inseridos)

    return janela, inseridos


def _faixas(identificadores) -> list[list[int]]:
    """[[início, fim], ...] de ids consecutivos: a carga do Censo cabe numa faixa só."""
    faixas: list[list[int]] = []
    for identificador in sorted(set(identificadores)):
        if faixas and identificador == faixas[-1][1] + 1:
            faixas[-1][1] = identificador
        else:
            faixas.append([identificador, identificador])

    return faixas


def _expandir(faixas: list[list[int]]) -> set[int]:
    return {identificador for inicio, fim in faixas for identificador in range(inicio, fim + 1)}


def _ler_controle(configuracao: Configuracao) -> dict:
    arquivo = arquivo_de_controle(configuracao)
    if not arquivo.exists():
        return {}

    return json.loads(arquivo.read_text(encoding="utf-8"))


def _gravar_controle(configuracao: Configuracao, controle: dict) -> None:
    arquivo = arquivo_de_controle(configuracao)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_suffix(".json.tmp")
    temporario.write_text(json.dumps(controle, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporario, arquivo)


def resumo_texto(relatorio: Relatorio) -> str:
    linhas = [
        f"marca d'água: {relatorio.marca_anterior.isoformat() if relatorio.marca_anterior else 'nenhuma (primeira execução)'}"
        f" -> {relatorio.marca_nova.isoformat() if relatorio.marca_nova else 'nenhuma'}",
        f"vistorias novas desde a marca: {len(relatorio.eventos)}",
        f"itens que a fonte canônica ainda não tinha (o CDC não entregara): {relatorio.completados}",
    ]
    for janela in relatorio.janelas:
        confere = "confere com a origem" if janela.confere_com_a_origem else "NÃO confere com a origem"
        linhas.append(f"  janela {janela.mes}: {janela.linhas} linhas, impressão {janela.impressao}, {confere}")
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
