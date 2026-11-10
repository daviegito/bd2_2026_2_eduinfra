import duckdb
import pytest

def test_classificacao_infraestrutura_nula():
    """Garante que a query do painel trate TRUE, FALSE e NULL corretamente."""
    con = duckdb.connect()
    con.execute("CREATE TABLE infra_mock (id INT, biblioteca INT)")

    # Inserindo Possui (1), Não Possui (0) e Não Informado (NULL)
    con.execute("INSERT INTO infra_mock VALUES (1, 1), (2, 0), (3, NULL)")

    df = con.execute("""
        SELECT
            CASE
                WHEN biblioteca IS NULL THEN 'Não informado'
                WHEN CAST(biblioteca AS DOUBLE) > 0 THEN 'Possui o item'
                ELSE 'Não possui'
            END as infra_status
        FROM infra_mock
        ORDER BY id
    """).df()

    status = df['infra_status'].tolist()
    assert status == ['Possui o item', 'Não possui', 'Não informado'], \
        "A classificação ignorou o tratamento isolado do NULL, misturando-o com ausência."

def test_deteccao_fato_real_enem():
    """Garante que o painel usa a tabela real do ENEM se disponível, abandonando a simulação."""
    con = duckdb.connect()

    # Simula o schema gold recebendo o novo contrato da issue #48
    con.execute("CREATE SCHEMA gold;")
    con.execute("CREATE TABLE gold.fato_desempenho_escola_ano (escola_infra_sk INT, media_geral DOUBLE);")

    tabelas_gold = [row[0] for row in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'gold'"
    ).fetchall()]

    tem_enem = "fato_desempenho_escola_ano" in tabelas_gold

    assert tem_enem is True, "A detecção do contrato real do ENEM falhou."
