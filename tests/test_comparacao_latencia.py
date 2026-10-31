import pytest

from eduinfra.comparacao_latencia import (
    PERIODOS_DE_RECARGA_S,
    VISTORIAS_POR_DIA_ESTIMADAS,
    ResumoDeTempos,
    derivar_latencias,
    projetar_wal,
    resumir,
)


@pytest.mark.parametrize(
    ("tempos", "mediana", "p95"),
    [
        ([1.0, 3.0], 2.0, 2.9),
        ([5.0, 5.0, 5.0], 5.0, 5.0),
        ([float(n) for n in range(1, 101)], 50.5, 95.05),
    ],
)
def test_resumo_calcula_mediana_e_p95_sem_extrapolar(tempos, mediana, p95):
    resumo = resumir(tempos)

    assert resumo.amostras == len(tempos)
    assert resumo.mediana_s == pytest.approx(mediana)
    assert resumo.p95_s == pytest.approx(p95)
    assert resumo.minimo_s <= resumo.p95_s <= resumo.maximo_s


@pytest.mark.parametrize("tempos", [[], [1.0]])
def test_resumo_recusa_amostras_insuficientes_para_p95(tempos):
    with pytest.raises(ValueError, match="amostras"):
        resumir(tempos)


def test_recarga_espera_meio_periodo_em_media_e_o_periodo_inteiro_no_pior_caso():
    execucao = ResumoDeTempos(amostras=10, mediana_s=0.5, p95_s=1.0, minimo_s=0.1, maximo_s=2.0)

    latencias = derivar_latencias(execucao)

    assert [latencia.periodo_s for latencia in latencias] == list(PERIODOS_DE_RECARGA_S)
    for latencia in latencias:
        assert latencia.media_s == pytest.approx(latencia.periodo_s / 2 + execucao.mediana_s)
        assert latencia.pior_caso_s == pytest.approx(latencia.periodo_s + execucao.p95_s)


def test_projecao_de_wal_divide_o_limite_pelo_volume_diario():
    custo = projetar_wal(bytes_acumulados=20_000, vistorias=2, limite_em_bytes=VISTORIAS_POR_DIA_ESTIMADAS * 10_000 * 3)

    assert custo.bytes_por_vistoria == 10_000
    assert custo.dias_ate_o_limite == pytest.approx(3)


@pytest.mark.parametrize("bytes_acumulados", [0, -1])
def test_projecao_de_wal_recusa_wal_que_nao_cresceu(bytes_acumulados):
    with pytest.raises(ValueError, match="WAL"):
        projetar_wal(bytes_acumulados, vistorias=1, limite_em_bytes=1)
