"""Publicação transacional e leitura das métricas versionadas do catálogo."""

import hashlib
import json
import re
from pathlib import Path

import psycopg
import yaml
from psycopg import sql

CATALOGO = Path(__file__).resolve().parents[2] / "metricas"
IDENTIFICADOR = re.compile(r"^[a-z][a-z0-9_]*$")


def carregar_definicoes(pasta: Path = CATALOGO) -> dict[str, dict]:
    definicoes = {}
    for arquivo in sorted(pasta.glob("*.yml")):
        definicao = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
        if definicao["status"] != "ativa":
            continue
        nome = definicao["nome"]
        if not IDENTIFICADOR.fullmatch(nome) or nome != arquivo.stem:
            raise ValueError(f"Nome inválido no catálogo: {arquivo.name}")
        definicoes[nome] = definicao
    return definicoes


def _hash_definicao(definicao: dict, pasta: Path = CATALOGO) -> str:
    contrato = {"versao_interface": 2, "definicao": definicao,
                "sql": {p.name: p.read_text(encoding="utf-8") for p in sorted((pasta / "sql").glob("*.sql"))}}
    conteudo = json.dumps(contrato, sort_keys=True, default=str).encode()
    return hashlib.sha256(conteudo).hexdigest()


def corpo_consulta(definicao: dict) -> str:
    interface = definicao["interface"]
    parametros = sql.Literal(interface["parametros"]).as_string()
    exige_item = sql.Literal(interface["item_obrigatorio"]).as_string()
    formula = definicao["formula"].replace("{{ minimo }}", str(int(definicao["supressao"]["minimo"])))
    return (
        "with parametros as materialized (select * from metricas.validar_filtros("
        f"p_filtros, {parametros}::text[], {exige_item})) "
        "select coalesce(jsonb_agg(to_jsonb(resultado)), '[]'::jsonb) "
        f"from ({formula}) resultado"
    )


def _preparar_publicador(conexao) -> list[str]:
    leitores = [r[0] for r in conexao.execute(
        "select rolname from pg_roles where rolname in ('eduinfra_painel', 'eduinfra_analise') order by rolname"
    )]
    if conexao.execute("select exists(select 1 from pg_roles where rolname='eduinfra_dbt')").fetchone()[0]:
        # O #110 não normaliza donos de funções publicadas antes da migração.
        superusuario = conexao.execute("select rolsuper from pg_roles where rolname=current_user").fetchone()[0]
        tem_definicoes = conexao.execute("select to_regclass('metricas.definicoes')").fetchone()[0]
        if superusuario and tem_definicoes:
            funcoes = conexao.execute(
                "select p.oid::regprocedure::text from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
                "where n.nspname='metricas' and (p.proname='validar_filtros' or "
                "p.proname in (select nome from metricas.definicoes))"
            ).fetchall()
            for assinatura, in funcoes:
                conexao.execute(sql.SQL("alter function {} owner to eduinfra_dbt").format(sql.SQL(assinatura)))
        conexao.execute("set local role eduinfra_dbt")
    if not conexao.execute("select exists(select 1 from pg_namespace where nspname='metricas')").fetchone()[0]:
        conexao.execute("create schema metricas")
    for leitor in leitores:
        conexao.execute(sql.SQL("alter default privileges in schema metricas revoke select on tables from {}").format(sql.Identifier(leitor)))
    return leitores


def _publicar_bases(conexao, pasta: Path) -> bool:
    com_enem = all(conexao.execute("select to_regclass(%s)", (nome,)).fetchone()[0]
                   for nome in ('gold.fato_desempenho_escola_ano', 'entrada_transformacao.enem_escola_ano'))
    for nome in ("base_analise", "base_cobertura"):
        arquivo = nome if com_enem else nome + "_sem_enem"
        consulta = (pasta / "sql" / f"{arquivo}.sql").read_text(encoding="utf-8")
        conexao.execute(sql.SQL("create or replace view metricas.{} as ").format(sql.Identifier(nome)) + sql.SQL(consulta))
    conexao.execute((pasta / "sql" / "validar_filtros.sql").read_text(encoding="utf-8"))
    return com_enem


def _conceder_interface(conexao, nomes: list[str], leitores: list[str]) -> None:
    papeis = ["public", *leitores]
    for papel in papeis:
        destinatario = sql.SQL("public") if papel == "public" else sql.Identifier(papel)
        conexao.execute(sql.SQL("revoke all on metricas.base_analise, metricas.base_cobertura from {}").format(destinatario))
        conexao.execute(sql.SQL("revoke all on function metricas.validar_filtros(jsonb,text[],boolean) from {}").format(destinatario))
        for nome in nomes:
            conexao.execute(sql.SQL("revoke all on function metricas.{}(jsonb) from {}").format(sql.Identifier(nome), destinatario))
    conexao.execute("revoke all on metricas.definicoes from public")
    for leitor in leitores:
        conexao.execute(sql.SQL("grant usage on schema metricas to {}").format(sql.Identifier(leitor)))
        conexao.execute(sql.SQL("grant select on metricas.definicoes to {}").format(sql.Identifier(leitor)))
        for nome in nomes:
            conexao.execute(sql.SQL("grant execute on function metricas.{}(jsonb) to {}").format(sql.Identifier(nome), sql.Identifier(leitor)))



def preparar_acesso(dsn: str) -> None:
    """Normaliza uma publicação anterior na etapa administrativa de migração."""
    with psycopg.connect(dsn) as conexao:
        if conexao.execute("select to_regclass('metricas.definicoes')").fetchone()[0] is None:
            return
        leitores = _preparar_publicador(conexao)
        nomes = [r[0] for r in conexao.execute("select nome from metricas.definicoes")]
        _conceder_interface(conexao, nomes, leitores)


def publicar(dsn: str, pasta: Path = CATALOGO) -> list[str]:
    definicoes = carregar_definicoes(pasta)
    if not definicoes:
        raise ValueError("Catálogo sem métricas ativas")
    with psycopg.connect(dsn) as conexao:
        leitores = _preparar_publicador(conexao)
        anteriores = {r[0] for r in conexao.execute("select nome from metricas.definicoes")} if conexao.execute(
            "select to_regclass('metricas.definicoes')").fetchone()[0] else set()
        com_enem = _publicar_bases(conexao, pasta)
        if not com_enem:
            definicoes = {n: d for n, d in definicoes.items() if n == 'pct_escolas_por_situacao_do_item'}
        if not definicoes:
            raise ValueError("Catálogo sem métricas disponíveis para as fontes da Gold")
        registros = []
        for nome, definicao in definicoes.items():
            corpo = corpo_consulta(definicao)
            corpo_funcao = (
                "begin perform * from metricas.validar_filtros(p_filtros, "
                + sql.Literal(definicao["interface"]["parametros"]).as_string()
                + "::text[], " + sql.Literal(definicao["interface"]["item_obrigatorio"]).as_string()
                + "); return (" + corpo + "); end"
            )
            # Só SQL versionado entra no corpo; o leitor não recebe acesso às bases.
            conexao.execute(sql.SQL(
                "create or replace function metricas.{}(p_filtros jsonb) returns jsonb "
                "language plpgsql stable security definer set search_path = pg_catalog, pg_temp as {}"
            ).format(sql.Identifier(nome), sql.Literal(corpo_funcao)))
            registros.append(sql.SQL("select {}::text as nome, {}::text as versao, {}::text as sha256").format(
                sql.Literal(nome), sql.Literal(str(definicao["versao"])),
                sql.Literal(_hash_definicao(definicao, pasta)),
            ))
        for nome in anteriores - definicoes.keys():
            conexao.execute(sql.SQL("drop function if exists metricas.{}(jsonb)").format(sql.Identifier(nome)))
        conexao.execute(sql.SQL("create or replace view metricas.definicoes as ") + sql.SQL(" union all ").join(registros))
        _conceder_interface(conexao, list(definicoes), leitores)
    return list(definicoes)


def consultar(dsn: str, nome: str, filtros: dict, pasta: Path = CATALOGO) -> dict:
    definicoes = carregar_definicoes(pasta)
    if nome not in definicoes:
        raise ValueError(f"Métrica ativa não encontrada: {nome}")
    if not isinstance(filtros, dict):
        raise ValueError("Filtros devem ser um objeto JSON")
    definicao = definicoes[nome]
    with psycopg.connect(dsn) as conexao:
        publicado = conexao.execute("select versao, sha256 from metricas.definicoes where nome = %s", (nome,)).fetchone()
        if publicado is None:
            raise RuntimeError("Métrica indisponível nas fontes publicadas; confira a Gold e a presença do ENEM")
        esperado = _hash_definicao(definicao, pasta)
        if publicado != (str(definicao["versao"]), esperado):
            raise RuntimeError("Catálogo mudou: execute publicar-metricas após construir a Gold")
        dados = conexao.execute(sql.SQL("select metricas.{}(%s::jsonb)").format(sql.Identifier(nome)),
                                (json.dumps(filtros, allow_nan=False),)).fetchone()[0]
    return {"metrica": nome, "versao": str(definicao["versao"]), "filtros": filtros, "dados": dados}
