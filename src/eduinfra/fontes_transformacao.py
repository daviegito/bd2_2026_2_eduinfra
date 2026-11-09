"""Ponte entre arquivos analíticos e as sources PostgreSQL do dbt.

Uma execução copia versões fixas das entradas numa transação. O dbt lê esse
retrato, nunca as tabelas de evento do OLTP. O chamador mantém o bloqueio de
sessão até terminar a transformação para impedir a troca das entradas no meio.
"""

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import psycopg
from deltalake import DeltaTable
from psycopg import sql

from eduinfra.configuracao import Configuracao

SCHEMA = "entrada_transformacao"
BLOQUEIO = 480026
COLUNAS = {
    "censo_municipio": {
        "co_municipio": "integer primary key", "no_municipio": "text not null",
        "sg_uf": "char(2) not null", "co_uf": "smallint not null",
    },
    "censo_escola": {
        "co_entidade": "integer primary key", "no_entidade": "text not null",
        "co_municipio": "integer not null", "tp_dependencia": "smallint not null",
        "tp_localizacao": "smallint not null", "ano_censo_referencia": "smallint not null",
    },
    "item_infraestrutura": {
        "codigo": "text primary key", "descricao": "text not null", "categoria": "text not null",
    },
    "eventos_vistoria": {
        "vistoria_id": "bigint not null", "item_codigo": "text not null",
        "co_entidade": "integer not null", "origem": "text not null",
        "ocorrido_em": "timestamptz not null", "registrado_em": "timestamptz not null",
        "situacao": "text not null", "quantidade": "integer",
    },
    "enem_escola_ano": {
        "co_entidade": "integer not null", "ano": "smallint not null",
        "data_referencia_infraestrutura": "date not null", "n_participantes": "integer not null",
        "media_cn": "numeric", "media_ch": "numeric", "media_lc": "numeric",
        "media_mt": "numeric", "media_redacao": "numeric",
        "n_participantes_sem_nota": "integer not null",
        "n_participantes_com_todas_as_notas": "integer not null",
        **{f"notas_validas_{area}": "integer not null" for area in ("cn", "ch", "lc", "mt", "redacao")},
        "redacoes_nota_zero": "integer not null", "media_geral": "numeric",
        "poucos_participantes": "boolean not null",
    },
}
RESTRICOES = {
    "eventos_vistoria": """,
        primary key (vistoria_id, item_codigo),
        check (situacao in ('existente', 'inexistente', 'em_obra', 'inutilizavel', 'nao_informado')),
        check (quantidade is null or quantidade >= 0),
        check (ocorrido_em <= registrado_em)
    """,
    "enem_escola_ano": """,
        primary key (co_entidade, ano),
        check (n_participantes > 0),
        check (n_participantes_sem_nota between 0 and n_participantes),
        check (extract(year from data_referencia_infraestrutura) = ano),
        check (n_participantes_com_todas_as_notas between 0 and n_participantes),
        check (redacoes_nota_zero between 0 and notas_validas_redacao)
    """,
}


@dataclass(frozen=True)
class Entradas:
    silver: Path
    eventos: Path
    enem: Path | None = None
    referencia_enem: date = date(2024, 11, 3)

    @classmethod
    def do_ambiente(cls, configuracao: Configuracao) -> "Entradas":
        dados = configuracao.diretorio_dados
        enem = os.environ.get("FONTE_ENEM")
        referencia = date.fromisoformat(os.environ.get("DATA_REFERENCIA_ENEM") or "2024-11-03")
        return cls(
            dados / "silver",
            Path(os.environ.get("FONTE_EVENTOS") or dados / "warehouse" / "eventos_vistoria"),
            Path(enem) if enem else dados / "silver" / "enem" / f"ano={configuracao.ano_enem}" / "desempenho_escola.parquet",
            referencia,
        )


@contextmanager
def bloquear(dsn: str):
    """Serializa preparo + build + docs; a sessão solta o lock mesmo após falha."""
    with psycopg.connect(dsn, autocommit=True) as conexao:
        if not conexao.execute("select pg_try_advisory_lock(%s)", (BLOQUEIO,)).fetchone()[0]:
            raise RuntimeError("outra transformação está usando as entradas; tente depois")
        try:
            yield conexao
        finally:
            conexao.execute("select pg_advisory_unlock(%s)", (BLOQUEIO,))


def _consulta_parquet(arquivos: list[Path], colunas: dict[str, str]) -> tuple[duckdb.DuckDBPyConnection, str]:
    leitor = duckdb.connect()
    leitor.read_parquet([str(p) for p in arquivos], hive_partitioning=False).create_view("entrada")
    presentes = {r[0] for r in leitor.execute("describe entrada").fetchall()}
    faltantes = set(colunas) - presentes
    if faltantes:
        leitor.close()
        raise RuntimeError(f"colunas ausentes na fonte Parquet: {', '.join(sorted(faltantes))}")
    nomes = ", ".join(f'"{c}"' for c in colunas)
    return leitor, nomes


def _copiar_parquet(conexao, tabela: str, arquivos: list[Path]) -> int:
    leitor, nomes = _consulta_parquet(arquivos, COLUNAS[tabela])
    try:
        consulta = f"select {nomes} from entrada"
        if tabela == "censo_municipio":
            consulta = f"select distinct {nomes} from entrada"
        if tabela == "censo_escola":
            conflito = leitor.execute(
                f"select co_entidade, ano_censo_referencia from (select distinct {nomes} from entrada) "
                "group by 1, 2 having count(*) > 1 limit 1"
            ).fetchone()
            if conflito:
                raise RuntimeError(f"cadastros conflitantes para escola/ano: {conflito}")
            consulta = (
                f"select {nomes} from entrada qualify row_number() over "
                "(partition by co_entidade order by ano_censo_referencia desc, no_entidade) = 1"
            )
        leitor.execute(consulta)
        with conexao.cursor().copy(sql.SQL("copy {}.{} from stdin").format(
            sql.Identifier(SCHEMA), sql.Identifier(tabela)
        )) as copia:
            while lote := leitor.fetchmany(50_000):
                for linha in lote:
                    copia.write_row(linha)
        return conexao.execute(sql.SQL("select count(*) from {}.{}").format(
            sql.Identifier(SCHEMA), sql.Identifier(tabela)
        )).fetchone()[0]
    finally:
        leitor.close()


MAPA_ENEM = {
    "co_entidade": "co_escola", "n_participantes": "participantes",
    "n_participantes_sem_nota": "participantes_sem_nota_valida",
    "n_participantes_com_todas_as_notas": "participantes_com_todas_as_notas",
}


def _copiar_enem(conexao, entradas: Entradas) -> int:
    caminho = entradas.enem
    arquivos = sorted(caminho.rglob("desempenho_escola.parquet")) if caminho.is_dir() else [caminho]
    if not arquivos:
        raise RuntimeError("desempenho_escola.parquet ausente na Silver do ENEM")
    exigidas = {MAPA_ENEM.get(nome, nome): tipo for nome, tipo in COLUNAS["enem_escola_ano"].items()
                if nome != "data_referencia_infraestrutura"}
    leitor, _ = _consulta_parquet(arquivos, exigidas)
    try:
        colunas = [f'"{MAPA_ENEM.get(nome, nome)}"' if nome != "data_referencia_infraestrutura"
                   else f"date '{entradas.referencia_enem.isoformat()}'"
                   for nome in COLUNAS["enem_escola_ano"]]
        leitor.execute("select " + ", ".join(colunas) + " from entrada")
        with conexao.cursor().copy(f"copy {SCHEMA}.enem_escola_ano from stdin") as copia:
            while lote := leitor.fetchmany(50_000):
                for linha in lote:
                    copia.write_row(linha)
        return conexao.execute(f"select count(*) from {SCHEMA}.enem_escola_ano").fetchone()[0]
    finally:
        leitor.close()


def _cobertura_enem(conexao) -> dict:
    colunas = ("escolas_enem", "escolas_com_censo", "escolas_sem_censo", "poucos_sem_censo",
               "participantes", "participantes_com_censo")
    valores = conexao.execute(f"""
        select count(*), count(*) filter (where c.co_entidade is not null),
               count(*) filter (where c.co_entidade is null),
               count(*) filter (where c.co_entidade is null and e.poucos_participantes),
               coalesce(sum(e.n_participantes), 0),
               coalesce(sum(e.n_participantes) filter (where c.co_entidade is not null), 0)
        from {SCHEMA}.enem_escola_ano e
        left join {SCHEMA}.censo_escola c using (co_entidade)
    """).fetchone()
    return dict(zip(colunas, valores))


def _copiar_eventos(conexao, caminho: Path, tabela_delta: DeltaTable | None = None) -> tuple[int, int | None]:
    if not caminho.is_dir() or not (caminho / "_delta_log").exists():
        arquivos = sorted(caminho.rglob("*.parquet")) if caminho.is_dir() else [caminho]
        return _copiar_parquet(conexao, "eventos_vistoria", arquivos), None
    tabela = tabela_delta if tabela_delta is not None else DeltaTable(str(caminho))
    # O dataset mantém a versão do log mesmo que o CDC confirme outra versão.
    dataset = tabela.to_pyarrow_dataset()
    faltantes = set(COLUNAS["eventos_vistoria"]) - set(dataset.schema.names)
    if faltantes:
        raise RuntimeError(f"colunas ausentes em eventos: {', '.join(sorted(faltantes))}")
    total = 0
    with conexao.cursor().copy(f"copy {SCHEMA}.eventos_vistoria from stdin") as copia:
        for lote in dataset.to_batches(columns=list(COLUNAS["eventos_vistoria"]), batch_size=50_000):
            colunas = [coluna.to_pylist() for coluna in lote.columns]
            for linha in zip(*colunas):
                copia.write_row(linha)
            total += lote.num_rows
    return total, tabela.version()


def preparar(conexao: psycopg.Connection, entradas: Entradas) -> dict:
    """Publica todas as sources juntas ou preserva integralmente a execução anterior."""
    arquivos = {
        nome: sorted(entradas.silver.glob(f"ano_censo=*/{arquivo}.parquet"))
        for nome, arquivo in [("censo_escola", "escola"), ("censo_municipio", "municipio")]
    }
    for nome, caminhos in arquivos.items():
        if not caminhos:
            raise RuntimeError(f"Silver ausente para {nome}: rode ingerir antes da transformação")
    if not entradas.eventos.exists():
        raise RuntimeError("fonte de eventos ausente: publique o Delta ou configure FONTE_EVENTOS (issue #45)")
    if entradas.enem is not None and not entradas.enem.exists():
        raise RuntimeError("FONTE_ENEM não existe; disponibilize desempenho_escola.parquet ou use a ingestão padrão")
    marcadores = {p: p.read_bytes() for p in entradas.silver.glob("ano_censo=*/origem.sha256")}
    if entradas.enem:
        diretorio = entradas.enem if entradas.enem.is_dir() else entradas.enem.parent
        marcador = diretorio / "origem.sha256"
        if marcador.exists():
            marcadores[marcador] = marcador.read_bytes()
    resumo = {"com_enem": entradas.enem is not None, "linhas": {}}
    tabelas = list(COLUNAS) if entradas.enem else [t for t in COLUNAS if t != "enem_escola_ano"]
    # O Delta precisa preceder o snapshot PG: o CDC só publica eventos já confirmados
    # na origem. Inverter a ordem pode fazer um evento válido parecer inexistente.
    tabela_delta = DeltaTable(str(entradas.eventos)) if (entradas.eventos / "_delta_log").is_dir() else None
    with conexao.transaction():
        conexao.execute("set transaction isolation level repeatable read")
        conexao.execute("set local TimeZone = 'UTC'")
        conexao.execute(f"create schema if not exists {SCHEMA}")
        for tabela in tabelas:
            definicao = ", ".join(f"{nome} {tipo}" for nome, tipo in COLUNAS[tabela].items())
            # As views de staging são recriadas pelo build; a Gold materializada não depende delas.
            conexao.execute(f"drop table if exists {SCHEMA}.{tabela} cascade")
            conexao.execute(f"create unlogged table {SCHEMA}.{tabela} ({definicao}{RESTRICOES.get(tabela, '')})")
        for tabela, caminhos in arquivos.items():
            resumo["linhas"][tabela] = _copiar_parquet(conexao, tabela, caminhos)
        conexao.execute(f"insert into {SCHEMA}.item_infraestrutura select codigo, descricao, categoria from public.item_infraestrutura")
        codigos = conexao.execute(f"select codigo from {SCHEMA}.item_infraestrutura").fetchall()
        if not codigos or any(not re.fullmatch(r"[a-z_]+", c[0]) for c in codigos):
            raise RuntimeError("catálogo vazio ou com código inválido; aplique as migrações da origem")
        total, versao = _copiar_eventos(conexao, entradas.eventos, tabela_delta)
        resumo["linhas"]["eventos_vistoria"] = total
        resumo["versao_delta"] = versao
        if entradas.enem:
            resumo["linhas"]["enem_escola_ano"] = _copiar_enem(conexao, entradas)
            resumo["cobertura_enem"] = _cobertura_enem(conexao)
            resumo["referencia_enem"] = entradas.referencia_enem.isoformat()
        sem_origem = conexao.execute(f"""
            select count(*) from {SCHEMA}.eventos_vistoria e
            where not exists (select 1 from public.vistoria_item i
                              where i.vistoria_id = e.vistoria_id and i.item_codigo = e.item_codigo)
        """).fetchone()[0]
        if sem_origem:
            raise RuntimeError(f"a fonte canônica contém {sem_origem} eventos sem chave na origem")
        if any(p.read_bytes() != valor for p, valor in marcadores.items()):
            raise RuntimeError("a versão da Silver mudou durante a leitura; reexecute a transformação")
        for tabela in tabelas:
            conexao.execute(f"analyze {SCHEMA}.{tabela}")
    return resumo
