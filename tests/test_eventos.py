from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pytest

from eduinfra import eventos
from eduinfra.configuracao import Configuracao

INSTANTE = datetime(2024, 5, 29, tzinfo=timezone.utc)


@pytest.fixture
def configuracao(tmp_path: Path) -> Configuracao:
    return Configuracao(
        dsn="",
        url_censo="",
        ano_censo=2024,
        data_referencia=INSTANTE.date(),
        diretorio_dados=tmp_path,
        arquivo_alembic=tmp_path / "alembic.ini",
    )


def _evento(vistoria_id: int, item: str, mes: str = "2024-05") -> dict:
    return {
        "vistoria_id": vistoria_id,
        "item_codigo": item,
        "co_entidade": 53002580,
        "sg_uf": "DF",
        "origem": "vistoria_presencial",
        "ocorrido_em": INSTANTE,
        "registrado_em": INSTANTE,
        "situacao": "existente",
        "quantidade": None,
        "mes_ocorrencia": mes,
        "lsn_origem": None,
    }


def _tabela(linhas: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(linhas, schema=eventos.ESQUEMA)


def test_primeira_gravacao_insere_tudo(configuracao):
    resultado = eventos.gravar_sem_duplicar(configuracao, _tabela([_evento(1, "biblioteca"), _evento(1, "quadra")]))

    assert (resultado.recebidas, resultado.inseridas) == (2, 2)
    assert eventos.contar(configuracao) == 2


def test_regravar_o_mesmo_lote_nao_duplica(configuracao):
    lote = _tabela([_evento(1, "biblioteca"), _evento(1, "quadra")])
    eventos.gravar_sem_duplicar(configuracao, lote)

    segunda = eventos.gravar_sem_duplicar(configuracao, lote)

    assert segunda.inseridas == 0
    assert segunda.descartadas_por_duplicidade == 2
    assert eventos.contar(configuracao) == 2


def test_lote_parcialmente_repetido_insere_so_o_novo(configuracao):
    eventos.gravar_sem_duplicar(configuracao, _tabela([_evento(1, "biblioteca")]))

    resultado = eventos.gravar_sem_duplicar(configuracao, _tabela([_evento(1, "biblioteca"), _evento(2, "biblioteca")]))

    assert resultado.inseridas == 1
    assert eventos.contar(configuracao) == 2


def test_mesma_chave_em_meses_diferentes_sao_eventos_distintos_da_particao(configuracao):
    eventos.gravar_sem_duplicar(configuracao, _tabela([_evento(1, "biblioteca", "2024-05")]))

    resultado = eventos.gravar_sem_duplicar(configuracao, _tabela([_evento(2, "biblioteca", "2024-06")]))

    assert resultado.inseridas == 1


def test_lote_vazio_nao_cria_versao(configuracao):
    resultado = eventos.gravar_sem_duplicar(configuracao, _tabela([]))

    assert (resultado.recebidas, resultado.inseridas) == (0, 0)
