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


def _hash_definicao(definicao: dict) -> str:
    conteudo = json.dumps(definicao, sort_keys=True, default=str).encode()
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


def publicar(dsn: str, pasta: Path = CATALOGO) -> list[str]:
    definicoes = carregar_definicoes(pasta)
    with psycopg.connect(dsn) as conexao:
        conexao.execute("create schema if not exists metricas")
        for nome in ("base_analise", "base_cobertura"):
            consulta = (pasta / "sql" / f"{nome}.sql").read_text(encoding="utf-8")
            conexao.execute(sql.SQL("create or replace view metricas.{} as ").format(sql.Identifier(nome)) + sql.SQL(consulta))
        conexao.execute((pasta / "sql" / "validar_filtros.sql").read_text(encoding="utf-8"))
        registros = []
        for nome, definicao in definicoes.items():
            corpo = corpo_consulta(definicao)
            # PL/pgSQL preserva a validação mesmo quando a consulta não encontra linhas.
            corpo_funcao = (
                "begin perform * from metricas.validar_filtros(p_filtros, "
                + sql.Literal(definicao["interface"]["parametros"]).as_string()
                + "::text[], " + sql.Literal(definicao["interface"]["item_obrigatorio"]).as_string()
                + "); return (" + corpo + "); end"
            )
            conexao.execute(sql.SQL(
                "create or replace function metricas.{}(p_filtros jsonb) returns jsonb "
                "language plpgsql stable security invoker as {}"
            ).format(sql.Identifier(nome), sql.Literal(corpo_funcao)))
            registros.append(sql.SQL("select {}::text as nome, {}::text as versao, {}::text as sha256").format(
                sql.Literal(nome), sql.Literal(str(definicao["versao"])),
                sql.Literal(_hash_definicao(definicao)),
            ))
        if not registros:
            raise ValueError("Catálogo sem métricas ativas")
        conexao.execute(sql.SQL("create or replace view metricas.definicoes as ") + sql.SQL(" union all ").join(registros))
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
        esperado = _hash_definicao(definicao)
        if publicado != (str(definicao["versao"]), esperado):
            raise RuntimeError("Catálogo mudou: execute publicar-metricas após construir a Gold")
        dados = conexao.execute(sql.SQL("select metricas.{}(%s::jsonb)").format(sql.Identifier(nome)),
                                (json.dumps(filtros, allow_nan=False),)).fetchone()[0]
    return {"metrica": nome, "versao": str(definicao["versao"]), "filtros": filtros, "dados": dados}
