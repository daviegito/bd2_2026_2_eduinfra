"""Recuperação seletiva e recibos de tratamento, sem apagar o histórico da DLQ."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg
import pyarrow as pa
import pyarrow.dataset as ds
from deltalake import DeltaTable, write_deltalake
from psycopg.rows import dict_row

from eduinfra import eventos, fila_de_erros, fluxo_cdc
from eduinfra.configuracao import Configuracao

NOME_DA_TABELA = "tratamentos_cdc"
MOTIVOS = ("esquema_corrigido", "falha_transitoria_resolvida", "desvio_revisado", "legado_revisado")
ESQUEMA = pa.schema([
    pa.field("lsn_origem", pa.int64(), nullable=False),
    pa.field("chave", pa.string(), nullable=False),
    pa.field("acao", pa.string(), nullable=False),
    pa.field("responsavel", pa.string(), nullable=False),
    pa.field("motivo", pa.string(), nullable=False),
    pa.field("tratado_em", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("estado_origem", pa.string(), nullable=False),
    pa.field("estado_delta", pa.string(), nullable=False),
    pa.field("chaves_revisadas", pa.string(), nullable=False),
])

# A chave primária de vistoria_item restringe a leitura; não se usa a linha de base inteira.
EVENTO_POR_CHAVE = """
select v.id as vistoria_id, i.item_codigo, v.co_entidade, m.sg_uf, v.origem,
       v.ocorrido_em, v.registrado_em, i.situacao::text, i.quantidade
from vistoria_item i
join vistoria v on v.id = i.vistoria_id
join escola e on e.co_entidade = v.co_entidade
join municipio m on m.co_municipio = e.co_municipio
where i.vistoria_id = %s and i.item_codigo = %s
"""


@dataclass(frozen=True)
class Resultado:
    recuperadas: int
    ja_tratadas: int
    pendentes: int
    linhas_lidas: int


def validar_operador(responsavel: str, motivo: str) -> None:
    if not re.fullmatch(r"operador-[0-9]{2,6}", responsavel or ""):
        raise ValueError("Use um identificador operacional como operador-01, sem nome ou e-mail.")
    if motivo not in MOTIVOS:
        raise ValueError("Escolha um motivo padronizado: " + ", ".join(MOTIVOS))


def caminho(config: Configuracao) -> str:
    return str(config.diretorio_dados / "warehouse" / NOME_DA_TABELA)


def ler_tratamentos(config: Configuracao) -> pa.Table:
    if not DeltaTable.is_deltatable(caminho(config)):
        return ESQUEMA.empty_table()
    return DeltaTable(caminho(config)).to_pyarrow_table()


def identidade(linha: dict) -> tuple[int, str]:
    return linha["lsn_origem"], linha["chave"]


def _resumo(linhas: list) -> str:
    def serializar(valor):
        if isinstance(valor, datetime):
            return valor.astimezone(timezone.utc).isoformat(timespec="microseconds")
        raise TypeError(type(valor).__name__)
    conteudo = json.dumps(linhas, default=serializar, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(conteudo.encode()).hexdigest()


def _filtro(entrada: dict):
    if entrada["tabela"] not in ("vistoria", "vistoria_item"):
        raise ValueError("Tabela fora do contrato do CDC.")
    if entrada["operacao"] == "truncate":
        return "true", (), None
    id = entrada["vistoria_id"]
    if id is None:
        raise ValueError("Ocorrência sem chave suficiente para um tratamento seletivo.")
    filtro = ds.field("vistoria_id") == id
    if entrada["tabela"] == "vistoria":
        return "v.id = %s", (id,), filtro
    item = entrada["item_codigo"]
    if item is None:
        raise ValueError("Ocorrência de item sem item_codigo.")
    return "v.id = %s and i.item_codigo = %s", (id, item), filtro & (ds.field("item_codigo").cast(pa.string()) == pa.scalar(item, type=pa.string()))


def _estado(conn, config: Configuracao, entrada: dict, chaves_revisadas: set[tuple] | None = None) -> tuple[str, str, set[tuple]]:
    condicao, parametros, filtro = _filtro(entrada)
    # Só valores analíticos entram na impressão; técnico e observação ficam de fora.
    origem = conn.execute(f"""
        select v.id, i.item_codigo, v.co_entidade, m.sg_uf, v.origem,
               (v.ocorrido_em at time zone 'UTC')::text,
               (v.registrado_em at time zone 'UTC')::text, i.situacao::text, i.quantidade
        from vistoria v join vistoria_item i on i.vistoria_id = v.id
        join escola e on e.co_entidade = v.co_entidade
        join municipio m on m.co_municipio = e.co_municipio
        where {condicao} order by v.id, i.item_codigo
    """, parametros).fetchall()
    destino = []
    if DeltaTable.is_deltatable(str(eventos.caminho_da_tabela(config))):
        tabela = DeltaTable(str(eventos.caminho_da_tabela(config))).to_pyarrow_dataset()
        destino = tabela.to_table(filter=filtro).to_pylist()
    if chaves_revisadas is not None:
        origem = [linha for linha in origem if (linha[0], linha[1]) in chaves_revisadas]
        destino = [linha for linha in destino if (linha["vistoria_id"], linha["item_codigo"]) in chaves_revisadas]
    destino.sort(key=lambda linha: (linha["vistoria_id"], linha["item_codigo"]))
    chaves = {(linha[0], linha[1]) for linha in origem}
    chaves.update((linha["vistoria_id"], linha["item_codigo"]) for linha in destino)
    return _resumo(origem), _resumo(destino), chaves


def _gravar(config, entrada, acao, responsavel, motivo, estado):
    linha = {"lsn_origem": entrada["lsn_origem"], "chave": entrada["chave"],
             "acao": acao, "responsavel": responsavel, "motivo": motivo,
             "tratado_em": datetime.now(timezone.utc),
             "estado_origem": estado[0], "estado_delta": estado[1],
             "chaves_revisadas": json.dumps(sorted(estado[2]))}
    destino = caminho(config)
    if not DeltaTable.is_deltatable(destino):
        (config.diretorio_dados / "warehouse").mkdir(parents=True, exist_ok=True)
        write_deltalake(destino, ESQUEMA.empty_table())
    # Repetição preserva o primeiro recibo, inclusive autor e instante originais.
    (DeltaTable(destino).merge(pa.Table.from_pylist([linha], schema=ESQUEMA),
        predicate="atual.lsn_origem = novo.lsn_origem and atual.chave = novo.chave",
        source_alias="novo", target_alias="atual").when_not_matched_insert_all().execute())


def _selecionar(config, lsn, chave):
    if (lsn is None) != (chave is None):
        raise ValueError("Informe --lsn e --chave juntos.")
    # Um lote legado pode conter mais de uma linha para a mesma ocorrência.
    fila = list({identidade(e): e for e in fila_de_erros.ler(config).to_pylist()}.values())
    if lsn is not None:
        fila = [linha for linha in fila if identidade(linha) == (lsn, chave)]
        if not fila:
            raise ValueError("Ocorrência não encontrada na fila de erros.")
    return sorted(fila, key=identidade)


def _conexao(config):
    conn = psycopg.connect(config.dsn)
    conn.execute("set transaction isolation level repeatable read, read only")
    conn.execute("set local time zone 'UTC'")
    return conn


def _ler_evento_no_delta(config: Configuracao, evento: dict) -> list[dict]:
    caminho_delta = str(eventos.caminho_da_tabela(config))
    if not DeltaTable.is_deltatable(caminho_delta):
        return []
    item = ds.field("item_codigo").cast(pa.string())
    filtro = (ds.field("vistoria_id") == evento["vistoria_id"]) & (item == evento["item_codigo"])
    return DeltaTable(caminho_delta).to_pyarrow_dataset().to_table(filter=filtro).to_pylist()


def reprocessar(config: Configuracao, responsavel: str, motivo: str, lsn=None, chave=None) -> Resultado:
    validar_operador(responsavel, motivo)
    entradas = [e for e in _selecionar(config, lsn, chave) if e["operacao"] == "insert"]
    if lsn is not None and not entradas:
        raise ValueError("Use tratar-desvio-cdc para uma alteração fora do insert-only.")
    recibos = {identidade(r) for r in ler_tratamentos(config).to_pylist()}
    recuperadas = repetidas = pendentes = lidas = 0
    with _conexao(config) as conn:
        for entrada in entradas:
            if identidade(entrada) in recibos:
                repetidas += 1
                continue
            try:
                with conn.cursor(row_factory=dict_row) as cursor:
                    evento = cursor.execute(EVENTO_POR_CHAVE,
                        (entrada["vistoria_id"], entrada["item_codigo"])).fetchone()
                if evento is None:
                    pendentes += 1
                    continue
                lidas += 1
                if evento["ocorrido_em"] is not None:
                    data = evento["ocorrido_em"].astimezone(timezone.utc)
                    evento["mes_ocorrencia"] = f"{data.year:04d}-{data.month:02d}"
                evento["lsn_origem"] = entrada["lsn_origem"]
                if any(evento.get(coluna) is None for coluna in fluxo_cdc.COLUNAS_OBRIGATORIAS):
                    pendentes += 1
                    continue
                tabela = pa.Table.from_pylist([evento], schema=eventos.ESQUEMA)
            except (psycopg.DataError, pa.ArrowException, ValueError, OverflowError, TypeError):
                pendentes += 1
                continue
            # O merge só procura no mês do evento; a chave pode existir em outro mês.
            gravadas = _ler_evento_no_delta(config, evento)
            if not gravadas:
                eventos.gravar_sem_duplicar(config, tabela)
                gravadas = _ler_evento_no_delta(config, evento)
            colunas = [nome for nome in eventos.ESQUEMA.names if nome != "lsn_origem"]
            if len(gravadas) != 1 or any(gravadas[0][nome] != evento[nome] for nome in colunas):
                pendentes += 1
                continue
            estado = (_resumo([{nome: evento[nome] for nome in colunas}]), _resumo(gravadas),
                      {(evento["vistoria_id"], evento["item_codigo"])})
            _gravar(config, entrada, "reprocessar", responsavel, motivo, estado)
            recuperadas += 1
    return Resultado(recuperadas, repetidas, pendentes, lidas)


def tratar_desvio(config: Configuracao, responsavel: str, motivo: str, lsn: int, chave: str) -> bool:
    validar_operador(responsavel, motivo)
    if lsn is None or chave is None:
        raise ValueError("O tratamento exige --lsn e --chave da ocorrência exata.")
    entrada, = _selecionar(config, lsn, chave)
    if entrada["operacao"] not in ("update", "delete", "truncate"):
        raise ValueError("Itens rejeitados exigem reprocessamento bem-sucedido, não aceite manual.")
    if identidade(entrada) in {identidade(r) for r in ler_tratamentos(config).to_pylist()}:
        return False
    with _conexao(config) as conn:
        _gravar(config, entrada, "aceitar_desvio", responsavel, motivo, _estado(conn, config, entrada))
    return True


def tratamentos_ativos(config: Configuracao, fila: pa.Table) -> tuple[set[tuple], set[tuple]]:
    """Preserva o tratamento histórico; só dispensa o checksum do estado exato aceito."""
    entradas = {identidade(e): e for e in fila.to_pylist()}
    ativos, chaves_aceitas = set(), set()
    recibos = ler_tratamentos(config).to_pylist()
    if not recibos:
        return ativos, chaves_aceitas
    with _conexao(config) as conn:
        for recibo in recibos:
            entrada = entradas.get(identidade(recibo))
            if entrada is None:
                continue
            ativos.add(identidade(entrada))
            if recibo["acao"] == "aceitar_desvio":
                origem, delta, chaves = _estado(conn, config, entrada, {tuple(c) for c in json.loads(recibo["chaves_revisadas"])})
                if (origem, delta) == (recibo["estado_origem"], recibo["estado_delta"]):
                    chaves_aceitas.update(chaves)
    return ativos, chaves_aceitas
