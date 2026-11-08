import pytest

from eduinfra.comparacao_latencia import (
    PERIODOS_DE_RECARGA_S,
    VISTORIAS_POR_DIA_ESTIMADAS,
    MedidaDaOrdem,
    derivar_latencias,
    formatar_ordem,
    projetar_wal,
)
from eduinfra.telemetria_cdc import ResumoDaOrdem, ResumoDeTempos


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


def test_relatorio_de_ordem_declara_que_a_carga_e_sintetica_e_mostra_as_perdas():
    ordem = ResumoDaOrdem(
        transacoes=40,
        reentregas=0,
        inversoes_de_lsn=0,
        inversoes_de_registro=10,
        maior_inversao_de_registro_s=0.75,
        fora_de_ordem_no_evento=10,
        alem_da_tolerancia=0,
    )

    relatorio = formatar_ordem(MedidaDaOrdem(ordem, perdas_sem_margem=3, perdas_com_margem=0, latencias={}))

    assert "sintética" in relatorio
    assert "10 (25,0%)" in relatorio
    assert "perderia 3 transações sem margem e 0 com a margem" in relatorio
