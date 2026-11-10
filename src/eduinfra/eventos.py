"""Fonte canônica dos eventos de vistoria, em Delta Lake (ADR 0008).

A chave natural do evento é (vistoria_id, item_codigo), a chave primária de
vistoria_item na origem. Toda escrita passa por um merge que só insere o que a
tabela ainda não tem: a linha de base, o CDC e as janelas de dado atrasado
gravam aqui, podem se sobrepor e reentregar sem duplicar linha. A partição é o
mês de ocorrido_em, a janela do dado atrasado. A transformação (Gold) lê só esta
tabela.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError

from eduinfra.configuracao import Configuracao

log = logging.getLogger(__name__)

NOME_DA_TABELA = "eventos_vistoria"

ESQUEMA = pa.schema(
    [
        pa.field("vistoria_id", pa.int64(), nullable=False),
        pa.field("item_codigo", pa.string(), nullable=False),
        pa.field("co_entidade", pa.int32(), nullable=False),
        pa.field("sg_uf", pa.string(), nullable=False),
        pa.field("origem", pa.string(), nullable=False),
        pa.field("ocorrido_em", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("registrado_em", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("situacao", pa.string(), nullable=False),
        pa.field("quantidade", pa.int32()),
        pa.field("mes_ocorrencia", pa.string(), nullable=False),
        pa.field("lsn_origem", pa.int64()),
    ]
)

COLUNAS_DA_CHAVE = ("vistoria_id", "item_codigo")
COLUNA_DE_PARTICAO = "mes_ocorrencia"

# O CDC e as janelas fazem merge na mesma tabela. Dois merges no mesmo mês entram em
# conflito no commit; repetir é seguro, porque o merge só insere o que falta.
TENTATIVAS_DE_GRAVACAO = 5
ESPERA_ENTRE_GRAVACOES_S = 0.5

# Impressão de um evento, igual seja qual for o caminho que o trouxe: lsn_origem fica
# de fora, porque a origem não o conhece e ele distingue os caminhos em vez de
# descrever o evento.
CONTEUDO = """
concat_ws('|', vistoria_id, item_codigo, co_entidade, sg_uf, origem, ocorrido_em::varchar,
          registrado_em::varchar, situacao, coalesce(quantidade::varchar, 'nulo'), mes_ocorrencia)
"""
IMPRESSAO = f"md5_number({CONTEUDO})"

LINHA_DE_BASE = """
select v.id::bigint as vistoria_id, i.item_codigo, v.co_entidade::int as co_entidade, m.sg_uf,
       v.origem, v.ocorrido_em, v.registrado_em, i.situacao::text as situacao,
       i.quantidade::int as quantidade,
       strftime(v.ocorrido_em at time zone 'UTC', '%Y-%m') as mes_ocorrencia,
       null::bigint as lsn_origem
from origem.public.vistoria v
join origem.public.vistoria_item i on i.vistoria_id = v.id
join origem.public.escola e on e.co_entidade = v.co_entidade
join origem.public.municipio m on m.co_municipio = e.co_municipio
"""


@dataclass(frozen=True)
class ResultadoDaGravacao:
    recebidas: int
    inseridas: int

    @property
    def descartadas_por_duplicidade(self) -> int:
        return self.recebidas - self.inseridas


def caminho_da_tabela(configuracao: Configuracao) -> Path:
    return configuracao.diretorio_dados / "warehouse" / NOME_DA_TABELA


def garantir_tabela(configuracao: Configuracao) -> Path:
    caminho = caminho_da_tabela(configuracao)
    if DeltaTable.is_deltatable(str(caminho)):
        return caminho

    caminho.parent.mkdir(parents=True, exist_ok=True)
    write_deltalake(str(caminho), ESQUEMA.empty_table(), partition_by=[COLUNA_DE_PARTICAO])
    log.info("tabela Delta %s criada em %s", NOME_DA_TABELA, caminho)
    return caminho


def gravar_sem_duplicar(configuracao: Configuracao, eventos: pa.Table) -> ResultadoDaGravacao:
    if eventos.num_rows == 0:
        return ResultadoDaGravacao(0, 0)

    caminho = garantir_tabela(configuracao)
    meses = sorted(set(eventos.column(COLUNA_DE_PARTICAO).to_pylist()))
    for tentativa in range(1, TENTATIVAS_DE_GRAVACAO + 1):
        try:
            metricas = (
                DeltaTable(str(caminho))
                .merge(
                    eventos.cast(ESQUEMA),
                    predicate=_predicado(meses),
                    source_alias="novo",
                    target_alias="atual",
                )
                .when_not_matched_insert_all()
                .execute()
            )
            break
        except CommitFailedError:
            if tentativa == TENTATIVAS_DE_GRAVACAO:
                raise
            log.info("merge em %s concorreu com outro escritor; nova tentativa", ", ".join(meses))
            time.sleep(ESPERA_ENTRE_GRAVACOES_S * tentativa)

    return ResultadoDaGravacao(eventos.num_rows, int(metricas["num_target_rows_inserted"]))


def _predicado(meses: list[str]) -> str:
    chave = " and ".join(f"atual.{coluna} = novo.{coluna}" for coluna in COLUNAS_DA_CHAVE)
    lista = ", ".join(f"'{mes}'" for mes in meses)

    return f"{chave} and atual.{COLUNA_DE_PARTICAO} in ({lista})"


def ler_da_origem(configuracao: Configuracao, filtro: str | None = None) -> pa.Table:
    """Eventos da origem no formato da tabela; filtro é SQL sobre v (vistoria) e i (item)."""
    conexao = duckdb.connect()
    try:
        conexao.execute("install postgres; load postgres;")
        conexao.execute("set TimeZone = 'UTC'")
        dsn = configuracao.dsn.replace("'", "''")
        conexao.execute(f"attach '{dsn}' as origem (type postgres, read_only)")
        # Mantém validação e exportação no mesmo snapshot da origem.
        conexao.execute("begin transaction")
        invalidas = conexao.execute("""
            select * from postgres_query('origem', '
                select id, (ocorrido_em at time zone ''UTC'')::text as ocorrido_em,
                       count(*) over () as total
                from public.vistoria
                where not (isfinite(ocorrido_em)
                    and ocorrido_em >= timestamptz ''0001-01-01 00:00:00+00''
                    and ocorrido_em < timestamptz ''10000-01-01 00:00:00+00'')
                order by id limit 10
            ')
        """).fetchall()
        if invalidas:
            exemplos = "; ".join(f"id={id}, ocorrido_em={data} UTC" for id, data, _ in invalidas)
            raise RuntimeError(
                f"Publicação analítica interrompida: {invalidas[0][2]} vistoria(s) com "
                f"ocorrido_em fora dos anos 0001 a 9999 UTC ou não finito. Exemplos: {exemplos}. "
                "A tabela Delta não foi alterada."
            )
        consulta = LINHA_DE_BASE + (f"where {filtro}" if filtro else "")
        tabela = conexao.execute(consulta).arrow().read_all().cast(ESQUEMA)
        conexao.execute("commit")
        return tabela
    finally:
        conexao.close()


def ler(configuracao: Configuracao, mes: str | None = None) -> tuple[pa.Table, int]:
    """Uma versão da tabela, e qual: quem lê duas vezes sabe se leu o mesmo retrato."""
    tabela = DeltaTable(str(garantir_tabela(configuracao)))
    filtro = [(COLUNA_DE_PARTICAO, "=", mes)] if mes else None

    # A leitura devolve a coluna de partição no fim; a ordem do esquema vale para todos os caminhos.
    return tabela.to_pyarrow_table(filters=filtro).select(ESQUEMA.names).cast(ESQUEMA), tabela.version()


def impressao(eventos: pa.Table) -> str:
    """Soma das impressões das linhas: não depende da ordem e muda se qualquer valor mudar.

    Soma 64 bits do md5 por linha, e não os 128 de IMPRESSAO: a soma de md5_number vira
    DOUBLE, que arredonda e depende da ordem; a de UBIGINT é HUGEINT, exata.
    """
    conexao = duckdb.connect()
    try:
        # Os instantes viram texto na impressão: em UTC, ela não depende do fuso de quem calcula.
        conexao.execute("set TimeZone = 'UTC'")
        conexao.register("eventos", eventos)
        total = conexao.execute(f"select coalesce(sum(md5_number_lower({CONTEUDO})), 0) from eventos").fetchone()[0]
    finally:
        conexao.close()

    return format(int(total), "x")


def carregar_da_origem(configuracao: Configuracao) -> ResultadoDaGravacao:
    return gravar_sem_duplicar(configuracao, ler_da_origem(configuracao))


def contar(configuracao: Configuracao) -> int:
    caminho = garantir_tabela(configuracao)
    conexao = duckdb.connect()
    try:
        conexao.execute("install delta; load delta;")
        return conexao.execute(f"select count(*) from delta_scan('{caminho.as_posix()}')").fetchone()[0]
    finally:
        conexao.close()

