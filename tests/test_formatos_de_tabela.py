"""As alternativas do ADR 0003 sobre uma Silver em miniatura, sem banco.

Iceberg só roda com o PyIceberg instalado (uv run --with) e fora do Windows,
onde o PyIceberg não abre caminhos file:///C:/.
"""

import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from eduinfra.comparacao_falha_tabela import ANTES, COMPLETA, ILEGIVEL, PARCIAL, RECUPEROU, classificar, ler_durante_compactacao, matar_gravador
from eduinfra.comparacao_formato_tabela import carregar_dados, comparar, formatar_markdown, respostas_coincidem
from eduinfra.comparacao_leitor_delta import comparar as comparar_leitores
from eduinfra.formatos_de_tabela import Delta, Iceberg, ParquetPuro, dividir_em_lotes

ESCOLAS = 600
ITENS = ("lab_ciencias", *(f"item_{numero}" for numero in range(16)))


def _exige_iceberg() -> None:
    pytest.importorskip("pyiceberg")
    if sys.platform == "win32":
        pytest.skip("o PyIceberg não abre caminhos file:///C:/ no Windows")


@pytest.fixture(scope="module")
def silver(tmp_path_factory) -> Path:
    pasta = tmp_path_factory.mktemp("dados") / "silver"
    ano = pasta / "ano_censo=2024"
    ano.mkdir(parents=True)
    conexao = duckdb.connect()
    consultas = {
        "municipio": "select * from (values (5300108, 'DF'), (2927408, 'BA')) t(co_municipio, sg_uf)",
        "escola": f"""
            select n as co_entidade, case when n % 2 = 0 then 5300108 else 2927408 end as co_municipio,
                   1 + n % 4 as tp_dependencia, 1 + n % 2 as tp_localizacao, 2024 as ano_censo_referencia
            from generate_series(1, {ESCOLAS}) e(n)
        """,
        "vistoria_item": f"""
            select n as co_entidade, item as item_codigo,
                   case when n % 3 = 0 then 'inexistente' else 'existente' end as situacao
            from generate_series(1, {ESCOLAS}) e(n), unnest({list(ITENS)}) i(item)
        """,
    }
    for tabela, consulta in consultas.items():
        conexao.execute(f"copy ({consulta}) to '{(ano / f'{tabela}.parquet').as_posix()}' (format parquet)")
    conexao.close()

    return pasta


@pytest.fixture(scope="module")
def dados(silver) -> pa.Table:
    return carregar_dados(silver)


@pytest.fixture(params=[ParquetPuro, Delta, Iceberg], ids=["parquet", "delta", "iceberg"])
def formato(request, tmp_path, dados):
    if request.param is Iceberg:
        _exige_iceberg()
    tabela = request.param(tmp_path / "tabela")
    tabela.iniciar(dados.schema)

    return tabela


def _contar(formato) -> tuple[int, int]:
    conexao = duckdb.connect()
    formato.preparar(conexao)
    resultado = conexao.execute(
        f"select count(*), count(distinct (ano_censo, co_entidade, item_codigo)) from {formato.origem_de_leitura()}"
    ).fetchone()
    conexao.close()

    return resultado


def test_reaplicar_o_mesmo_lote_nao_duplica_linhas(formato, dados):
    base, lote = dividir_em_lotes(dados, 2)
    formato.acrescentar(base)

    formato.reaplicar(lote)
    formato.reaplicar(lote)

    assert _contar(formato) == (dados.num_rows, dados.num_rows)


def test_compactar_junta_os_arquivos_sem_mudar_as_linhas(formato, dados):
    for lote in dividir_em_lotes(dados, 4):
        formato.acrescentar(lote)
    arquivos_antes = formato.tamanho().arquivos

    formato.compactar()

    assert formato.tamanho().arquivos < arquivos_antes
    assert _contar(formato) == (dados.num_rows, dados.num_rows)


def test_os_tres_formatos_dao_as_mesmas_respostas_e_so_o_iceberg_nao_le_a_copia(silver):
    _exige_iceberg()

    comparacao = comparar(silver, repeticoes=1, lotes=2, itens_do_upsert=(34,))

    assert respostas_coincidem(comparacao)
    assert "NÃO" not in formatar_markdown(comparacao)
    assert all(medida.linhas == medida.chaves_distintas == ESCOLAS * len(ITENS) for medida in comparacao.reentrega)
    na_copia_padrao = {copia.formato: copia.linhas for copia in comparacao.copia if copia.forma == "padrão"}
    assert na_copia_padrao == {"A. Parquet puro": ESCOLAS * len(ITENS), "B. Delta": ESCOLAS * len(ITENS), "C. Iceberg": None}


@pytest.mark.parametrize(
    ("total", "esperado"),
    [(None, ILEGIVEL), (10, ANTES), (15, PARCIAL), (20, COMPLETA)],
)
def test_classificar_o_estado_da_tabela_depois_da_falha(total, esperado):
    assert classificar(total, linhas_antes=10, linhas_depois=20) == esperado


def test_gravador_morto_nunca_deixa_a_tabela_delta_parcial_e_reaplicar_recupera(silver):
    [resultado] = matar_gravador(silver, rodadas=1, classes=(Delta,))

    assert set(resultado.estados) <= {ANTES, COMPLETA}
    assert set(resultado.recuperacao) == {RECUPEROU}


def test_leitor_concorrente_da_tabela_delta_nunca_ve_contagem_errada(silver):
    [resultado] = ler_durante_compactacao(silver, rodadas=2, lotes=4, classes=(Delta,))

    assert resultado.leituras > 0
    assert resultado.contagem_errada == 0


def test_mesmos_arquivos_dao_as_mesmas_respostas_pelos_dois_leitores(silver):
    medidas = comparar_leitores(silver, repeticoes=1, lotes_por_cenario=(1, 2), arquivos_ordenados=3)

    assert {medida.cenario for medida in medidas} == {
        "Por UF, 1 gravação",
        "Por UF, 2 gravações",
        "Sem partição, ordenada por escola",
    }
    assert all(medida.respostas_iguais for medida in medidas)
