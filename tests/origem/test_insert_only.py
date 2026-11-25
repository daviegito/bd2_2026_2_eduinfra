"""Garantia central do esquema: o passado não pode ser reescrito (ADR 0001)."""

import os

import psycopg
import pytest

pytestmark = pytest.mark.integracao

DSN = os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def conexao():
    if not DSN:
        pytest.skip("DATABASE_URL não definida")

    with psycopg.connect(DSN) as aberta:
        yield aberta


@pytest.fixture
def vistoria_existente(conexao):
    resultado = conexao.execute("select id from vistoria order by id limit 1").fetchone()
    if not resultado:
        pytest.skip("banco sem carga")

    return resultado[0]


@pytest.mark.parametrize(
    "comando",
    [
        "update vistoria set observacao = 'reescrita' where id = %s",
        "delete from vistoria where id = %s",
    ],
)
def test_vistoria_rejeita_reescrita_do_passado(conexao, vistoria_existente, comando):
    with pytest.raises(psycopg.errors.RestrictViolation):
        with conexao.transaction():
            conexao.execute(comando, (vistoria_existente,))


@pytest.mark.parametrize(
    "comando",
    [
        "update vistoria_item set situacao = 'existente' where vistoria_id = %s",
        "delete from vistoria_item where vistoria_id = %s",
    ],
)
def test_item_rejeita_reescrita_do_passado(conexao, vistoria_existente, comando):
    with pytest.raises(psycopg.errors.RestrictViolation):
        with conexao.transaction():
            conexao.execute(comando, (vistoria_existente,))


def test_evento_nao_pode_ser_posterior_ao_registro(conexao, vistoria_existente):
    with pytest.raises(psycopg.errors.CheckViolation):
        with conexao.transaction():
            conexao.execute(
                """
                insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em)
                select co_entidade, tecnico_id, origem, now() + interval '1 day'
                from vistoria where id = %s
                """,
                (vistoria_existente,),
            )


def test_estado_atual_traz_um_registro_por_escola_e_item(conexao):
    duplicados = conexao.execute(
        """
        select count(*) from (
            select co_entidade, item_codigo
            from escola_estado_atual
            group by co_entidade, item_codigo
            having count(*) > 1
        ) repetidos
        """
    ).fetchone()[0]

    assert duplicados == 0
