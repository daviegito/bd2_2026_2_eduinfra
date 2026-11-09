"""Silver do ENEM e cobertura da junção sobre CSVs em miniatura, sem banco."""

from datetime import date
from pathlib import Path

import duckdb
import pytest

from eduinfra import cobertura_enem, comparacao_compressao, enem
from eduinfra.configuracao import Configuracao
from eduinfra.transformacao import particao_silver

CABECALHO = (
    "NU_SEQUENCIAL;NU_ANO;CO_ESCOLA;CO_MUNICIPIO_ESC;NO_MUNICIPIO_ESC;CO_UF_ESC;SG_UF_ESC;"
    "TP_DEPENDENCIA_ADM_ESC;TP_LOCALIZACAO_ESC;TP_SIT_FUNC_ESC;CO_MUNICIPIO_PROVA;NO_MUNICIPIO_PROVA;"
    "CO_UF_PROVA;SG_UF_PROVA;TP_PRESENCA_CN;TP_PRESENCA_CH;TP_PRESENCA_LC;TP_PRESENCA_MT;"
    "CO_PROVA_CN;CO_PROVA_CH;CO_PROVA_LC;CO_PROVA_MT;NU_NOTA_CN;NU_NOTA_CH;NU_NOTA_LC;NU_NOTA_MT;"
    "TX_RESPOSTAS_CN;TX_RESPOSTAS_CH;TX_RESPOSTAS_LC;TX_RESPOSTAS_MT;TP_LINGUA;TX_GABARITO_CN;"
    "TX_GABARITO_CH;TX_GABARITO_LC;TX_GABARITO_MT;TP_STATUS_REDACAO;NU_NOTA_COMP1;NU_NOTA_COMP2;"
    "NU_NOTA_COMP3;NU_NOTA_COMP4;NU_NOTA_COMP5;NU_NOTA_REDACAO"
)

ESCOLA_REAL = 53000001
ESCOLA_MASCARADA = 60000001


def linha(
    sequencial: int,
    escola: int | None,
    presenca: tuple[str, str, str, str],
    notas: tuple[str, str, str, str],
    status_redacao: str = "",
    redacao: str = "",
) -> str:
    """presenca e notas na ordem CN, CH, LC, MT; string vazia é campo em branco."""
    if escola is None:
        dados_escola = [""] * 8
    else:
        dados_escola = [str(escola), "5300108", "Brasília", "53", "DF", "2", "1", "1"]
    campos = [
        str(sequencial), "2024", *dados_escola, "5300108", "Brasília", "53", "DF",
        *presenca, "1420", "1383", "1395", "1408", *notas,
        "", "", "", "", "0", "", "", "", "",
        status_redacao, "", "", "", "", "", redacao,
    ]

    return ";".join(campos)


# Escola real: os cinco primeiros cobrem os critérios; os demais completam dez
# participantes, para a escola não ficar abaixo do limite de máscara do INEP.
CASOS_DA_ESCOLA_REAL = [
    linha(1, ESCOLA_REAL, ("1", "1", "1", "1"), ("500", "600", "550", "650"), "1", "700"),
    # Faltou ao segundo dia; redação anulada vale zero.
    linha(2, ESCOLA_REAL, ("0", "1", "1", "0"), ("", "400", "420", ""), "2", "0"),
    # Faltou aos dois dias: nenhuma nota válida.
    linha(3, ESCOLA_REAL, ("0", "0", "0", "0"), ("", "", "", "")),
    # Eliminado no primeiro dia, presente no segundo.
    linha(4, ESCOLA_REAL, ("1", "2", "2", "1"), ("480", "", "", "520")),
    # Presente com zero em CH (mantido) e nota de CN faltante (fora da média).
    linha(5, ESCOLA_REAL, ("1", "1", "1", "1"), ("", "0", "500", "600"), "1", "800"),
]
COMPLEMENTO = [
    linha(sequencial, ESCOLA_REAL, ("0", "0", "0", "0"), ("", "", "", "")) for sequencial in range(6, 11)
]
OUTRAS = [
    linha(11, ESCOLA_MASCARADA, ("1", "1", "1", "1"), ("400", "400", "400", "400"), "1", "400"),
    linha(12, None, ("1", "1", "1", "1"), ("700", "700", "700", "700"), "1", "900"),
]


def escrever_resultados(pasta: Path, linhas: list[str]) -> Path:
    destino = pasta / "bruto" / "enem"
    destino.mkdir(parents=True, exist_ok=True)
    arquivo = destino / "RESULTADOS_2024.csv"
    arquivo.write_text("\n".join([CABECALHO, *linhas]), encoding="latin-1")

    return arquivo


@pytest.fixture
def configuracao(tmp_path: Path) -> Configuracao:
    return Configuracao(
        dsn="postgresql://irrelevante",
        url_censo="https://exemplo.invalido/censo.zip",
        ano_censo=2024,
        data_referencia=date(2024, 5, 29),
        diretorio_dados=tmp_path / "dados",
        arquivo_alembic=tmp_path / "alembic.ini",
        url_enem="https://exemplo.invalido/enem.zip",
    )


@pytest.fixture
def resultados(configuracao) -> Path:
    return escrever_resultados(configuracao.diretorio_dados, [*CASOS_DA_ESCOLA_REAL, *COMPLEMENTO, *OUTRAS])


def ler(parquet: Path) -> list[dict]:
    cursor = duckdb.connect().execute(
        f"select * from read_parquet('{parquet.as_posix()}', hive_partitioning=false)"
    )
    nomes = [coluna[0] for coluna in cursor.description]

    return [dict(zip(nomes, valores)) for valores in cursor.fetchall()]


def publicar(configuracao: Configuracao, csv: Path) -> Path:
    _, preparo = enem.montar_silver_enem(configuracao, csv, "hash-de-teste")
    return enem.publicar_silver_enem(configuracao, preparo)


def test_agregacao_aplica_os_criterios_declarados(configuracao, resultados):
    silver, _ = enem.montar_silver_enem(configuracao, resultados, "hash-de-teste")
    escolas = {e["co_escola"]: e for e in ler(silver.desempenho)}

    assert set(escolas) == {ESCOLA_REAL, ESCOLA_MASCARADA}
    real = escolas[ESCOLA_REAL]
    assert real["participantes"] == 10
    # Ausente, eliminado e faltante ficam fora da média; o zero de CH entra.
    assert (real["notas_validas_cn"], real["media_cn"]) == (2, pytest.approx(490))
    assert (real["notas_validas_ch"], real["media_ch"]) == (3, pytest.approx(1000 / 3))
    assert (real["notas_validas_lc"], real["media_lc"]) == (3, pytest.approx(490))
    assert (real["notas_validas_mt"], real["media_mt"]) == (3, pytest.approx(590))
    # A redação anulada vale zero e é contada à parte.
    assert (real["notas_validas_redacao"], real["media_redacao"]) == (3, pytest.approx(500))
    assert real["redacoes_nota_zero"] == 1
    # Só o primeiro tem as cinco notas (o quinto perdeu CN por falta do dado).
    assert real["participantes_com_todas_as_notas"] == 1
    assert real["media_geral"] == pytest.approx((500 + 600 + 550 + 650 + 700) / 5)
    assert real["participantes_sem_nota_valida"] == 6
    assert not real["poucos_participantes"]
    assert escolas[ESCOLA_MASCARADA]["poucos_participantes"]


def test_resumo_conta_quem_ficou_sem_escola_e_os_casos_de_borda(configuracao, resultados):
    silver, _ = enem.montar_silver_enem(configuracao, resultados, "hash-de-teste")
    [resumo] = ler(silver.resumo)

    assert resumo["participantes"] == 12
    assert resumo["participantes_com_escola"] == 11
    assert resumo["escolas"] == 2
    assert resumo["notas_objetivas_faltantes"] == 1
    assert resumo["notas_objetivas_zero"] == 1
    assert resumo["redacoes_nota_zero"] == 1


def test_reexecutar_a_transformacao_produz_o_mesmo_conteudo(configuracao, resultados):
    primeira, _ = enem.montar_silver_enem(configuracao, resultados, "hash-de-teste")
    segunda, _ = enem.montar_silver_enem(configuracao, resultados, "hash-de-teste")

    assert ler(primeira.desempenho) == ler(segunda.desempenho)
    assert ler(primeira.resumo) == ler(segunda.resumo)


def test_particao_publicada_tem_marcador_e_nenhuma_sobra(configuracao, resultados):
    destino = publicar(configuracao, resultados)

    assert destino == configuracao.diretorio_dados / "silver" / "enem" / "ano=2024"
    assert sorted(p.name for p in destino.iterdir()) == [
        "desempenho_escola.parquet", "origem.sha256", "resumo.parquet",
    ]
    assert enem.hash_da_silver_enem(configuracao) == "hash-de-teste"
    assert list((configuracao.diretorio_dados / "_publicacao").iterdir()) == []


def test_layout_anterior_a_2024_e_recusado_com_mensagem_clara(configuracao):
    destino = configuracao.diretorio_dados / "bruto" / "enem"
    destino.mkdir(parents=True)
    antigo = destino / "RESULTADOS_2024.csv"
    antigo.write_text("NU_INSCRICAO;NU_ANO;CO_ESCOLA;NU_NOTA_CN\n1;2023;53000001;500", encoding="latin-1")

    with pytest.raises(RuntimeError, match="CO_MUNICIPIO_ESC"):
        enem.montar_silver_enem(configuracao, antigo, "hash-de-teste")
    assert list((configuracao.diretorio_dados / "_publicacao").iterdir()) == []


def escrever_silver_do_censo(configuracao: Configuracao, codigos: list[int]) -> None:
    particao = particao_silver(configuracao)
    particao.mkdir(parents=True)
    valores = ", ".join(f"({codigo}, 'ESCOLA')" for codigo in codigos)
    duckdb.connect().execute(
        f"copy (select * from (values {valores}) t(co_entidade, no_entidade)) "
        f"to '{(particao / 'escola.parquet').as_posix()}' (format parquet)"
    )


def test_cobertura_separa_codigo_mascarado_de_escola_ausente(configuracao, resultados):
    publicar(configuracao, resultados)
    escrever_silver_do_censo(configuracao, [ESCOLA_REAL, 53000099])

    cobertura = cobertura_enem.medir(configuracao)

    assert (cobertura.participantes, cobertura.participantes_com_escola) == (12, 11)
    assert (cobertura.escolas, cobertura.escolas_mascaradas, cobertura.escolas_no_censo) == (2, 1, 1)
    assert cobertura.participantes_em_escolas_no_censo == 10
    assert cobertura.escolas_fora_da_silver_do_censo is None
    assert {f.rotulo: (f.escolas, f.escolas_no_censo) for f in cobertura.faixas} == {"1": (1, 0), "10 a 29": (1, 1)}
    assert "| Escolas encontradas no Censo, sobre as de código real | 1 | 100,0% |" in cobertura_enem.formatar_markdown(cobertura)


def test_silver_do_enem_nao_confunde_os_leitores_da_silver_do_censo(configuracao, resultados):
    publicar(configuracao, resultados)
    escrever_silver_do_censo(configuracao, [ESCOLA_REAL])

    silver = configuracao.diretorio_dados / "silver"

    assert comparacao_compressao._localizar(silver, "escola") == particao_silver(configuracao) / "escola.parquet"
