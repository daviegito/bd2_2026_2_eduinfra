from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pytest

from eduinfra import eventos, telemetria_cdc
from eduinfra.configuracao import Configuracao
from eduinfra.pgoutput import OPERACAO_DELETE, OPERACAO_INSERT, OPERACAO_TRUNCATE, OPERACAO_UPDATE, Alteracao, Insercao, Relacao, Transacao
from eduinfra.telemetria_cdc import RelogioDaPassada, linhas_da_passada, perdas_por_marca_dagua, resumir, resumir_ordem

COMMIT = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
ITEM = Insercao("vistoria_item", {"vistoria_id": "1", "item_codigo": "quadra"})
VISTORIA = Insercao("vistoria", {"id": "1"})


def _segundos(valor: float) -> datetime:
    return COMMIT + timedelta(seconds=valor)


RELOGIO = RelogioDaPassada(_segundos(1), _segundos(1.5), _segundos(2), _segundos(4))


def _transacao(lsn: int = 100, **campos) -> Transacao:
    return Transacao(1, lsn, campos.pop("confirmada_em", COMMIT), campos.pop("insercoes", (VISTORIA, ITEM)), **campos)


def _evento(lsn: int, registrado_em: datetime, ocorrido_em: datetime) -> dict:
    return {
        "vistoria_id": 1,
        "item_codigo": "quadra",
        "co_entidade": 1,
        "sg_uf": "DF",
        "origem": "vistoria_presencial",
        "ocorrido_em": ocorrido_em,
        "registrado_em": registrado_em,
        "situacao": "existente",
        "quantidade": None,
        "mes_ocorrencia": "2026-10",
        "lsn_origem": lsn,
    }


def _eventos(*linhas: dict) -> pa.Table:
    return pa.Table.from_pylist(list(linhas), schema=eventos.ESQUEMA)


@pytest.fixture
def configuracao(tmp_path) -> Configuracao:
    return Configuracao("", "", 2024, COMMIT.date(), tmp_path, tmp_path / "alembic.ini")


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


def test_passada_decompoe_o_atraso_em_captura_e_carga():
    (linha,) = linhas_da_passada([_transacao()], RELOGIO, _eventos(), [])

    assert linha["espera_s"] == pytest.approx(1)
    assert linha["leitura_s"] == pytest.approx(0.5)
    assert linha["atraso_de_captura_s"] == pytest.approx(1.5)
    assert linha["conversao_s"] == pytest.approx(0.5)
    assert linha["gravacao_s"] == pytest.approx(2)
    assert linha["atraso_de_carga_s"] == pytest.approx(2.5)
    assert linha["ponta_a_ponta_s"] == pytest.approx(linha["atraso_de_captura_s"] + linha["atraso_de_carga_s"])


def test_commit_entre_o_relogio_e_a_leitura_nao_gera_espera_negativa():
    (linha,) = linhas_da_passada([_transacao(confirmada_em=_segundos(1.2))], RELOGIO, _eventos(), [])

    assert linha["espera_s"] == 0
    assert linha["atraso_de_captura_s"] == pytest.approx(0.3)


def test_linha_conta_cada_operacao_e_as_rejeicoes_da_propria_transacao():
    alteracoes = (
        Alteracao(OPERACAO_UPDATE, "vistoria_item", {}),
        Alteracao(OPERACAO_DELETE, "vistoria_item", {}),
        Alteracao(OPERACAO_DELETE, "vistoria", {}),
        Alteracao(OPERACAO_TRUNCATE, "vistoria_item", {}),
    )
    rejeicoes = [
        {"lsn_origem": 100, "operacao": OPERACAO_INSERT},
        {"lsn_origem": 100, "operacao": OPERACAO_UPDATE},
        {"lsn_origem": 999, "operacao": OPERACAO_INSERT},
    ]

    (linha,) = linhas_da_passada([_transacao(alteracoes=alteracoes)], RELOGIO, _eventos(), rejeicoes)

    assert (linha["vistorias"], linha["itens"]) == (1, 1)
    assert (linha["atualizacoes"], linha["exclusoes"], linha["truncamentos"]) == (1, 2, 1)
    assert linha["itens_rejeitados"] == 1


def test_linha_guarda_o_registro_mais_tardio_e_o_evento_mais_antigo_da_transacao():
    tabela = _eventos(
        _evento(100, _segundos(-5), _segundos(-90)),
        _evento(100, _segundos(-2), _segundos(-30)),
        _evento(200, _segundos(-1), _segundos(-1)),
    )

    (linha,) = linhas_da_passada([_transacao(100)], RELOGIO, tabela, [])

    assert linha["registrado_em"] == _segundos(-2)
    assert linha["ocorrido_em"] == _segundos(-90)


def test_linha_registra_desvio_de_esquema_anunciado_pelo_wal():
    relacao = Relacao("vistoria_item", {"vistoria_id": 20, "item_codigo": 25, "situacao": 16390, "quantidade": 23, "nova": 25})

    (linha,) = linhas_da_passada([_transacao(relacoes=(relacao,))], RELOGIO, _eventos(), [])

    assert linha["desvios_de_esquema"] == "vistoria_item.nova: coluna_nova"


def test_linha_sem_desvio_nao_carrega_texto():
    (linha,) = linhas_da_passada([_transacao()], RELOGIO, _eventos(), [])

    assert linha["desvios_de_esquema"] is None


def _chegada(lsn: int, registrado: float | None = 0, ocorrido: float | None = 0) -> dict:
    return {
        "lsn_fim": lsn,
        "registrado_em": None if registrado is None else _segundos(registrado),
        "ocorrido_em": None if ocorrido is None else _segundos(ocorrido),
    }


def test_chegada_em_ordem_nao_acusa_nada():
    ordem = resumir_ordem([_chegada(10, 1, 1), _chegada(20, 2, 2), _chegada(30, 3, 3)])

    assert ordem.transacoes == 3
    assert (ordem.reentregas, ordem.inversoes_de_lsn, ordem.inversoes_de_registro, ordem.fora_de_ordem_no_evento) == (0, 0, 0, 0)


def test_transacao_longa_inverte_o_registro_sem_inverter_o_lsn():
    ordem = resumir_ordem([_chegada(10, 5, 5), _chegada(20, 2, 2), _chegada(30, 6, 6)])

    assert ordem.inversoes_de_lsn == 0
    assert ordem.inversoes_de_registro == 1
    assert ordem.maior_inversao_de_registro_s == pytest.approx(3)
    assert ordem.fora_de_ordem_no_evento == 1


def test_reentrega_nao_conta_como_inversao():
    ordem = resumir_ordem([_chegada(10, 1, 1), _chegada(20, 2, 2), _chegada(10, 1, 1), _chegada(20, 2, 2)])

    assert ordem.transacoes == 2
    assert ordem.reentregas == 2
    assert (ordem.inversoes_de_lsn, ordem.inversoes_de_registro) == (0, 0)


def test_lsn_menor_ainda_nao_visto_e_inversao():
    ordem = resumir_ordem([_chegada(20), _chegada(10)])

    assert ordem.inversoes_de_lsn == 1


def test_evento_alem_da_tolerancia_do_adr_0004_e_contado():
    dias = telemetria_cdc.TOLERANCIA_DE_ATRASO.total_seconds()

    ordem = resumir_ordem([_chegada(10, 0, -dias - 1), _chegada(20, 1, 1 - dias)])

    assert ordem.alem_da_tolerancia == 1


def test_transacao_sem_item_nao_entra_nas_inversoes():
    ordem = resumir_ordem([_chegada(10, 5, 5), _chegada(20, None, None)])

    assert ordem.transacoes == 2
    assert ordem.inversoes_de_registro == 0


def _par(registrado: float, confirmado: float) -> tuple[datetime, datetime]:
    return _segundos(registrado), _segundos(confirmado)


@pytest.mark.parametrize(
    ("pares", "periodo_s", "margem_s", "perdidas"),
    [
        ([], 2, 0, 0),
        ([_par(0, 0.1), _par(3, 3.1), _par(6, 6.1)], 2, 0, 0),
        ([_par(0, 5), _par(1, 1.1)], 2, 0, 1),
        ([_par(0, 5), _par(1, 1.1)], 2, 600, 0),
        ([_par(0, 1.5), _par(1, 1.1)], 2, 0, 0),
    ],
    ids=["sem-transacoes", "sem-concorrencia", "transacao-longa-escapa", "margem-recupera", "mesmo-ciclo-nao-perde"],
)
def test_marca_dagua_perde_a_transacao_que_confirma_depois_da_marca(pares, periodo_s, margem_s, perdidas):
    assert perdas_por_marca_dagua(pares, periodo_s, margem_s) == perdidas


def test_frescor_volta_igual_ao_registrado(configuracao):
    telemetria_cdc.registrar_frescor(configuracao, COMMIT)

    assert telemetria_cdc.ler_frescor(configuracao) == COMMIT


def test_frescor_sem_registro_e_ausente(configuracao):
    assert telemetria_cdc.ler_frescor(configuracao) is None


def test_telemetria_gravada_e_filtrada_por_intervalo_de_lsn_em_ordem_de_chegada(configuracao):
    segunda = RelogioDaPassada(_segundos(5), _segundos(5.5), _segundos(6), _segundos(7))
    telemetria_cdc.registrar_passada(configuracao, linhas_da_passada([_transacao(100), _transacao(200)], RELOGIO, _eventos(), []))
    telemetria_cdc.registrar_passada(configuracao, linhas_da_passada([_transacao(300)], segunda, _eventos(), []))

    assert [linha["lsn_fim"] for linha in telemetria_cdc.ler_telemetria(configuracao)] == [100, 200, 300]
    assert [linha["lsn_fim"] for linha in telemetria_cdc.ler_telemetria(configuracao, 100, 200)] == [200]


def test_telemetria_sem_registro_e_vazia(configuracao):
    assert telemetria_cdc.ler_telemetria(configuracao) == []


def test_falha_ao_registrar_telemetria_nao_interrompe_a_entrega(configuracao, monkeypatch):
    def falhar(*_, **__):
        raise OSError("disco cheio")

    monkeypatch.setattr(telemetria_cdc, "write_deltalake", falhar)

    telemetria_cdc.registrar_passada(configuracao, linhas_da_passada([_transacao()], RELOGIO, _eventos(), []))

    assert telemetria_cdc.ler_telemetria(configuracao) == []
