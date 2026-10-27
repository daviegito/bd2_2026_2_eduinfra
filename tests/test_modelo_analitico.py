"""Modelo dimensional da Gold (E3): grão declarado, SCD tipo 2 e consistência com a origem.

Cada teste cria o próprio banco, porque a origem é insert-only e não há como limpar linhas.
"""

import hashlib
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from eduinfra import modelo_analitico

pytestmark = pytest.mark.integracao

DSN_ADMIN = os.environ.get("DATABASE_URL")
RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture
def dsn():
    if not DSN_ADMIN:
        pytest.skip("DATABASE_URL não definida")

    nome = f"eduinfra_gold_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(DSN_ADMIN, autocommit=True) as admin:
        admin.execute(f'create database "{nome}"')

    destino = urlsplit(DSN_ADMIN)._replace(path=f"/{nome}").geturl()
    os.environ["DATABASE_URL"] = destino
    try:
        command.upgrade(Config(str(RAIZ / "alembic.ini")), "head")
        _semear_cadastro(destino)
        yield destino
    finally:
        os.environ["DATABASE_URL"] = DSN_ADMIN
        with psycopg.connect(DSN_ADMIN, autocommit=True) as admin:
            admin.execute(f'drop database if exists "{nome}" with (force)')


def _semear_cadastro(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        conexao.execute("insert into tecnico (matricula, nome, lotacao_uf) values ('T1', 'Técnico', 'DF')")
        conexao.execute("insert into municipio values (5300108, 'Brasília', 'DF', 53), (5208707, 'Goiânia', 'GO', 52)")
        conexao.execute(
            """
            insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia)
            values (1, 'ESCOLA UM', 5300108, 2, 1, 2024),
                   (2, 'ESCOLA DOIS', 5300108, 4, 1, 2024),
                   (3, 'ESCOLA TRES', 5208707, 3, 2, 2024)
            """
        )


def vistoria(dsn: str, escola: int, ocorrido: str, itens: dict[str, str], origem: str = "vistoria_presencial") -> int:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        vistoria_id = conexao.execute(
            "insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em) "
            "values (%s, (select id from tecnico limit 1), %s, %s) returning id",
            (escola, origem, ocorrido),
        ).fetchone()[0]
        for item, situacao in itens.items():
            conexao.execute(
                "insert into vistoria_item (vistoria_id, item_codigo, situacao) values (%s, %s, %s)",
                (vistoria_id, item, situacao),
            )

    return vistoria_id


def consultar(dsn: str, sql: str, parametros: tuple = ()) -> list[tuple]:
    with psycopg.connect(dsn) as conexao:
        return conexao.execute(sql, parametros).fetchall()


def impressao_da_gold(dsn: str) -> str:
    resumo = []
    for tabela in modelo_analitico.TABELAS_DA_GOLD:
        # to_jsonb evita converter 'infinity' para datetime, que o Python não representa.
        linhas = consultar(dsn, f"select to_jsonb(t)::text from gold.{tabela} t order by 1")
        resumo.append((tabela, linhas))

    return hashlib.sha256(repr(resumo).encode()).hexdigest()


def historia_da_escola_um(dsn: str) -> None:
    """Censo 2024, uma revisita com mudança em 2025 e uma revisita que repete o estado."""
    vistoria(dsn, 1, "2024-05-29", {"biblioteca": "existente", "lab_ciencias": "inexistente", "internet": "nao_informado"},
             origem="censo_escolar")
    vistoria(dsn, 1, "2025-03-10", {"biblioteca": "existente", "lab_ciencias": "existente"})
    vistoria(dsn, 1, "2025-06-01", {"biblioteca": "existente", "lab_ciencias": "existente"})


def test_cada_mudanca_cria_uma_versao_e_a_anterior_continua_la(dsn):
    historia_da_escola_um(dsn)
    modelo_analitico.construir(dsn)

    versoes = consultar(
        dsn,
        "select valid_from::date as inicio, valid_to::text as fim, is_current, n_existentes, itens ->> 'lab_ciencias', valid_from::text as inicio_texto "
        "from gold.dim_escola_infraestrutura where co_entidade = 1 order by valid_from",
    )

    # A vistoria de 01/06/2025 repete o estado e não cria versão.
    assert len(versoes) == 2
    antiga, atual = versoes
    assert (str(antiga[0]), antiga[2], antiga[3], antiga[4]) == ("2024-05-29", False, 1, "inexistente")
    assert (str(atual[0]), atual[1], atual[2], atual[3], atual[4]) == ("2025-03-10", "infinity", True, 2, "existente")
    # O fim de uma versão é exatamente o começo da seguinte, sem lacuna.
    assert antiga[1] == atual[5]


def test_consulta_por_data_devolve_o_perfil_que_valia_naquele_dia(dsn):
    historia_da_escola_um(dsn)
    modelo_analitico.construir(dsn)

    sql = (
        "select itens ->> 'lab_ciencias' from gold.dim_escola_infraestrutura "
        "where co_entidade = 1 and valid_from <= %s and %s < valid_to"
    )

    assert consultar(dsn, sql, ("2025-01-01", "2025-01-01")) == [("inexistente",)]
    assert consultar(dsn, sql, ("2025-04-01", "2025-04-01")) == [("existente",)]
    assert consultar(dsn, sql, ("2024-01-01", "2024-01-01")) == []


def test_as_versoes_de_uma_escola_nao_se_sobrepoem_e_ha_uma_so_vigente(dsn):
    historia_da_escola_um(dsn)
    vistoria(dsn, 1, "2025-09-01", {"biblioteca": "inexistente"})
    vistoria(dsn, 2, "2024-05-29", {"biblioteca": "existente"}, origem="censo_escolar")
    modelo_analitico.construir(dsn)

    sobrepostas = consultar(
        dsn,
        """
        select count(*) from gold.dim_escola_infraestrutura a
        join gold.dim_escola_infraestrutura b
          on a.co_entidade = b.co_entidade and a.valid_from < b.valid_from and b.valid_from < a.valid_to
        """,
    )[0][0]
    vigentes = consultar(
        dsn, "select co_entidade, count(*) filter (where is_current) from gold.dim_escola_infraestrutura group by 1"
    )

    assert sobrepostas == 0
    assert all(quantidade == 1 for _, quantidade in vigentes)


def test_nao_informado_nao_apaga_o_que_ja_se_sabia(dsn):
    vistoria(dsn, 1, "2024-05-29", {"internet": "existente", "biblioteca": "existente"}, origem="censo_escolar")
    vistoria(dsn, 1, "2025-02-01", {"internet": "nao_informado", "biblioteca": "inexistente"})
    modelo_analitico.construir(dsn)

    versoes = consultar(
        dsn, "select itens ->> 'internet', itens ->> 'biblioteca' from gold.dim_escola_infraestrutura "
             "where co_entidade = 1 order by valid_from"
    )

    assert versoes == [("existente", "existente"), ("existente", "inexistente")]


def test_evento_atrasado_refaz_o_historico_sem_trocar_as_chaves_das_versoes_antigas(dsn):
    historia_da_escola_um(dsn)
    modelo_analitico.construir(dsn)
    chaves_antes = dict(consultar(dsn, "select escola_infra_sk, co_entidade::text || '@' || valid_from::text from gold.dim_escola_infraestrutura"))

    # Vistoria feita em janeiro de 2024 e digitada só agora: chega depois, ocorre antes.
    vistoria(dsn, 1, "2024-01-15", {"lab_ciencias": "existente"})
    modelo_analitico.construir(dsn)

    versoes = consultar(
        dsn, "select valid_from::date, itens ->> 'lab_ciencias' from gold.dim_escola_infraestrutura "
             "where co_entidade = 1 order by valid_from"
    )
    chaves_depois = dict(consultar(dsn, "select escola_infra_sk, co_entidade::text || '@' || valid_from::text from gold.dim_escola_infraestrutura"))

    assert [(str(v[0]), v[1]) for v in versoes] == [
        ("2024-01-15", "existente"),
        ("2024-05-29", "inexistente"),
        ("2025-03-10", "existente"),
    ]
    # Cada chave antiga continua apontando para a MESMA versão (escola e início), não só existindo.
    assert all(chaves_depois.get(chave) == versao for chave, versao in chaves_antes.items())


def test_fato_fino_tem_uma_linha_por_vistoria_e_item(dsn):
    historia_da_escola_um(dsn)
    modelo_analitico.construir(dsn)

    na_origem = consultar(dsn, "select count(*) from public.vistoria_item")[0][0]
    no_fato = consultar(dsn, "select count(*) from gold.fato_vistoria_item")[0][0]
    duplicadas = consultar(
        dsn, "select count(*) from (select 1 from gold.fato_vistoria_item group by vistoria_id, item_codigo having count(*) > 1) d"
    )[0][0]

    assert (no_fato, duplicadas) == (na_origem, 0)


def test_fato_largo_tem_uma_linha_por_escola_e_ano_e_aponta_para_a_versao_vigente_no_fim_do_ano(dsn):
    historia_da_escola_um(dsn)
    modelo_analitico.construir(dsn)

    linhas = consultar(
        dsn,
        """
        select f.ano, f.n_existentes, f.lab_ciencias, f.internet, d.valid_from::date, f.pct_existentes
        from gold.fato_infra_escola_ano f
        join gold.dim_escola_infraestrutura d using (escola_infra_sk)
        where f.co_entidade = 1 order by f.ano
        """,
    )

    assert [(l[0], l[1], l[2], l[3], str(l[4])) for l in linhas] == [
        (2024, 1, False, None, "2024-05-29"),
        (2025, 2, True, None, "2025-03-10"),
    ]
    assert float(linhas[0][5]) == 50.0


def test_fato_largo_bate_com_a_view_de_estado_atual_da_origem(dsn):
    for escola in (1, 2, 3):
        vistoria(dsn, escola, "2024-05-29", {"biblioteca": "existente", "lab_ciencias": "inexistente", "quadra_esportes": "existente"},
                 origem="censo_escolar")
    modelo_analitico.construir(dsn)

    divergencias = consultar(
        dsn,
        """
        select f.co_entidade
        from gold.fato_infra_escola_ano f
        left join (
            select co_entidade, count(*) as n from public.escola_estado_atual
            where situacao = 'existente' group by 1
        ) o using (co_entidade)
        where f.ano = 2024 and f.n_existentes <> coalesce(o.n, 0)
        """,
    )

    assert divergencias == []


def test_construir_duas_vezes_da_exatamente_o_mesmo_resultado(dsn):
    historia_da_escola_um(dsn)
    vistoria(dsn, 3, "2024-05-29", {"biblioteca": "existente"}, origem="censo_escolar")

    modelo_analitico.construir(dsn)
    primeira = impressao_da_gold(dsn)
    modelo_analitico.construir(dsn)

    assert impressao_da_gold(dsn) == primeira


def test_falha_na_construcao_preserva_a_gold_anterior(dsn, monkeypatch):
    historia_da_escola_um(dsn)
    modelo_analitico.construir(dsn)
    antes = impressao_da_gold(dsn)

    vistoria(dsn, 2, "2025-02-01", {"biblioteca": "existente"})
    etapas_reais = modelo_analitico._etapas

    def etapas_que_falham_no_fim(itens):
        etapas = etapas_reais(itens)
        etapas[-2] = lambda conexao: (_ for _ in ()).throw(RuntimeError("falha simulada no fato largo"))
        return etapas

    monkeypatch.setattr(modelo_analitico, "_etapas", etapas_que_falham_no_fim)

    with pytest.raises(RuntimeError, match="falha simulada"):
        modelo_analitico.construir(dsn)

    assert impressao_da_gold(dsn) == antes


def test_comparacao_de_granularidades_responde_igual_nos_dois_graos(dsn):
    for escola, rede_com_lab in ((1, "existente"), (2, "inexistente"), (3, "existente")):
        vistoria(dsn, escola, "2024-05-29", {"lab_ciencias": rede_com_lab, "biblioteca": "existente"}, origem="censo_escolar")
    modelo_analitico.construir(dsn)

    comparacao = modelo_analitico.comparar(dsn, repeticoes=2)
    texto = modelo_analitico.formatar_markdown(comparacao)

    assert comparacao.mesmo_resultado
    assert comparacao.fino.linhas == 6
    assert comparacao.largo.linhas == 3
    assert "uma linha por escola, por item, por vistoria" in texto
    assert "uma linha por escola por ano, um item por coluna" in texto
    assert "Os resultados são idênticos." in texto


def test_serie_historica_nao_muda_quando_a_dimensao_ganha_uma_versao_nova(dsn):
    vistoria(dsn, 1, "2024-05-29", {"biblioteca": "existente", "lab_ciencias": "inexistente"}, origem="censo_escolar")
    modelo_analitico.construir(dsn)
    consulta = (
        "select ano, escola_infra_sk, n_existentes, lab_ciencias "
        "from gold.fato_infra_escola_ano where co_entidade = 1 and ano = %s"
    )
    de_2024_antes = consultar(dsn, consulta, (2024,))

    # A escola passa a ter laboratório em 2025: a dimensão ganha uma versão e fecha a anterior.
    vistoria(dsn, 1, "2025-03-10", {"lab_ciencias": "existente"})
    modelo_analitico.construir(dsn)

    # O que já tinha sido dito sobre 2024 continua igual; 2025 nasce com o perfil novo.
    assert consultar(dsn, consulta, (2024,)) == de_2024_antes
    assert consultar(dsn, consulta, (2025,))[0][3] is True
    assert de_2024_antes[0][3] is False
