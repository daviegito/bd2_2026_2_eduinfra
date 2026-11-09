from dataclasses import replace
from datetime import datetime, timezone

import pytest

from eduinfra import auditoria_cdc, cdc, fila_de_erros
from eduinfra.auditoria_cdc import Auditoria, ComparacaoDeLinhas, SaldoDaOperacao, calcular_saldo, formatar, violacoes
from eduinfra.contrato_cdc import COLUNA_NOVA, Desvio
from eduinfra.pgoutput import OPERACAO_DELETE, OPERACAO_INSERT, OPERACAO_UPDATE, Alteracao, Insercao, Transacao
from eduinfra.telemetria_cdc import ResumoDaOrdem

INSTANTE = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
LINHAS_LIMPAS = ComparacaoDeLinhas(3_000, 17, 0, 0, 0, 0, 0, 0, [])
ORDEM_LIMPA = ResumoDaOrdem(1, 0, 0, 0, 0.0, 0, 0)
SALDO_LIMPO = [SaldoDaOperacao(OPERACAO_INSERT, 17, 17, 0, 0), SaldoDaOperacao(OPERACAO_UPDATE, 0, 0, 0, 0)]
SLOT_SAUDAVEL = cdc.EstadoDoSlot(True, "reserved", 0, 0)
AUDITORIA_LIMPA = Auditoria(
    linhas=LINHAS_LIMPAS,
    saldo=SALDO_LIMPO,
    ordem=ORDEM_LIMPA,
    latencias={},
    desvios_no_catalogo=[],
    desvios_vistos_no_wal={},
    fila_por_motivo={},
    drenado_em=INSTANTE,
    frescor_s=2.0,
    sla_de_frescor_s=60.0,
    slot=SLOT_SAUDAVEL,
)


def _linha_de_telemetria(lsn: int, itens: int = 0, atualizacoes: int = 0) -> dict:
    return {"lsn_fim": lsn, "itens": itens, "atualizacoes": atualizacoes, "exclusoes": 0, "truncamentos": 0}


def _fila(*operacoes: str):
    linhas = [fila_de_erros.montar_linha(1, INSTANTE, "vistoria_item", operacao, 1, str(n), "motivo") for n, operacao in enumerate(operacoes)]
    return fila_de_erros.pa.Table.from_pylist(linhas, schema=fila_de_erros.ESQUEMA)


def test_auditoria_limpa_nao_tem_violacao():
    assert violacoes(AUDITORIA_LIMPA) == []


@pytest.mark.parametrize(
    ("ajuste", "trecho"),
    [
        ({"linhas": replace(LINHAS_LIMPAS, diferentes_com_lsn=1)}, "conteúdo diferente"),
        ({"linhas": replace(LINHAS_LIMPAS, diferentes_sem_lsn=1)}, "conteúdo diferente"),
        ({"linhas": replace(LINHAS_LIMPAS, sem_explicacao=1)}, "fora do Delta"),
        ({"linhas": replace(LINHAS_LIMPAS, so_no_delta=1)}, "não existem mais"),
        ({"linhas": replace(LINHAS_LIMPAS, na_fila=1)}, "fila de erros"),
        ({"saldo": [SaldoDaOperacao(OPERACAO_DELETE, 1, 0, 1, 0)]}, "insert-only"),
        ({"saldo": [SaldoDaOperacao(OPERACAO_UPDATE, 0, 0, 0, 1)]}, "insert-only"),
        ({"ordem": replace(ORDEM_LIMPA, inversoes_de_lsn=1)}, "LSN invertido"),
        ({"desvios_no_catalogo": [Desvio("vistoria_item", "nova", COLUNA_NOVA)]}, "vistoria_item.nova"),
        ({"slot": cdc.EstadoDoSlot(False, None, 0, 0)}, "não existe"),
        ({"slot": cdc.EstadoDoSlot(True, "lost", 0, 0)}, "invalidado"),
        ({"frescor_s": None, "drenado_em": None}, "nunca registrou"),
        ({"frescor_s": 61.0}, "acima do SLA"),
    ],
)
def test_cada_problema_vira_uma_violacao(ajuste, trecho):
    (violacao,) = violacoes(replace(AUDITORIA_LIMPA, **ajuste))

    assert trecho in violacao


@pytest.mark.parametrize("ajuste", [{"linhas": replace(LINHAS_LIMPAS, pendentes=17)}, {"frescor_s": 60.0}, {"ordem": replace(ORDEM_LIMPA, reentregas=3, inversoes_de_registro=2)}])
def test_pendencia_reentrega_e_frescor_no_limite_nao_sao_violacao(ajuste):
    assert violacoes(replace(AUDITORIA_LIMPA, **ajuste)) == []


def test_saldo_conta_a_transacao_reentregue_uma_vez_so():
    telemetria = [_linha_de_telemetria(10, itens=17), _linha_de_telemetria(10, itens=17), _linha_de_telemetria(20, itens=17)]

    (insercoes, *_) = calcular_saldo(telemetria, aplicadas=34, fila=_fila(), pendencias=[])

    assert insercoes.lidas_do_wal == 34
    assert insercoes.ja_estavam_no_delta == 0


def test_saldo_separa_aplicada_fila_e_pendente_por_operacao():
    telemetria = [_linha_de_telemetria(10, itens=17, atualizacoes=2)]
    pendencia = Transacao(
        1,
        30,
        INSTANTE,
        (Insercao("vistoria_item", {}), Insercao("vistoria", {})),
        (Alteracao(OPERACAO_UPDATE, "vistoria_item", {}),),
    )

    insercoes, atualizacoes, exclusoes, _ = calcular_saldo(telemetria, aplicadas=15, fila=_fila(OPERACAO_INSERT, OPERACAO_UPDATE, OPERACAO_UPDATE), pendencias=[pendencia])

    assert (insercoes.lidas_do_wal, insercoes.aplicadas, insercoes.na_fila, insercoes.ja_estavam_no_delta, insercoes.pendentes) == (17, 15, 1, 1, 1)
    assert (atualizacoes.lidas_do_wal, atualizacoes.aplicadas, atualizacoes.na_fila, atualizacoes.ja_estavam_no_delta, atualizacoes.pendentes) == (2, 0, 2, 0, 1)
    assert exclusoes.lidas_do_wal == 0


def test_resumo_de_latencia_exige_ao_menos_duas_transacoes():
    assert auditoria_cdc.resumir_latencias([{}]) == {}


def test_relatorio_mostra_as_tres_secoes_e_o_resultado():
    relatorio = formatar(AUDITORIA_LIMPA)

    for trecho in ("## Consistência", "## Latência e frescor", "## Resiliência", "Nenhuma violação."):
        assert trecho in relatorio


def test_relatorio_lista_as_violacoes_e_as_divergencias_por_dia():
    divergente = replace(LINHAS_LIMPAS, sem_explicacao=2, divergencias_por_dia=[("2026-10-31", "so_na_origem", 2)])

    relatorio = formatar(replace(AUDITORIA_LIMPA, linhas=divergente))

    assert "| 2026-10-31 | so_na_origem | 2 |" in relatorio
    assert "- 2 linhas da origem fora do Delta" in relatorio
