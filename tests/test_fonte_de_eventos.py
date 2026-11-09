"""Fonte canônica de eventos (ADR 0008): um evento só, venha pelo CDC ou pelas janelas. Sem banco."""

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake.exceptions import CommitFailedError

from eduinfra import eventos, fluxo_cdc, janelas
from eduinfra.configuracao import Configuracao
from eduinfra.pgoutput import Insercao, Transacao

OCORRIDO = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
REGISTRADO = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
LSN = 900


@pytest.fixture
def configuracao(tmp_path: Path) -> Configuracao:
    return Configuracao(
        dsn="",
        url_censo="",
        ano_censo=2024,
        data_referencia=OCORRIDO.date(),
        diretorio_dados=tmp_path,
        arquivo_alembic=tmp_path / "alembic.ini",
    )


def pelo_cdc(item: str = "biblioteca", situacao: str = "existente") -> dict:
    """O evento como o consumidor do CDC o monta, a partir da mensagem do slot."""
    transacao = Transacao(1, LSN, REGISTRADO, ())
    insercao = Insercao("vistoria_item", {"vistoria_id": "7", "item_codigo": item, "situacao": situacao, "quantidade": None})
    cabecalho = (7, 53002580, "DF", "vistoria_presencial", OCORRIDO, REGISTRADO)

    return fluxo_cdc._evento(transacao, insercao, cabecalho)


def pelas_janelas(item: str = "biblioteca", situacao: str = "existente") -> dict:
    """O mesmo evento lido da origem pelas janelas: igual, mas sem LSN."""
    return pelo_cdc(item, situacao) | {"lsn_origem": None}


def tabela(*linhas: dict) -> pa.Table:
    return pa.Table.from_pylist(list(linhas), schema=eventos.ESQUEMA)


@pytest.mark.parametrize("primeiro", ["cdc", "janelas"])
def test_mesmo_evento_pelos_dois_caminhos_aparece_uma_vez(configuracao, primeiro):
    caminhos = {"cdc": tabela(pelo_cdc()), "janelas": tabela(pelas_janelas())}
    segundo = "janelas" if primeiro == "cdc" else "cdc"

    eventos.gravar_sem_duplicar(configuracao, caminhos[primeiro])
    repetido = eventos.gravar_sem_duplicar(configuracao, caminhos[segundo])
    na_fonte, _ = eventos.ler(configuracao)

    assert repetido.inseridas == 0
    assert na_fonte.num_rows == 1
    # O primeiro caminho a entregar fica registrado: o LSN diz se veio do CDC.
    assert na_fonte.column("lsn_origem").to_pylist() == [LSN if primeiro == "cdc" else None]


def test_evento_atrasado_cai_na_janela_de_ocorrido_em(configuracao):
    eventos.gravar_sem_duplicar(configuracao, tabela(pelo_cdc()))

    agosto, _ = eventos.ler(configuracao, "2026-08")
    outubro, _ = eventos.ler(configuracao, "2026-10")

    assert pelo_cdc()["mes_ocorrencia"] == "2026-08"
    assert (agosto.num_rows, outubro.num_rows) == (1, 0)
    assert (eventos.caminho_da_tabela(configuracao) / "mes_ocorrencia=2026-08").is_dir()


def test_impressao_nao_depende_do_caminho_nem_da_ordem():
    dois = [pelo_cdc("biblioteca"), pelo_cdc("quadra")]

    assert eventos.impressao(tabela(*dois)) == eventos.impressao(tabela(*[pelas_janelas(e["item_codigo"]) for e in dois]))
    assert eventos.impressao(tabela(*dois)) == eventos.impressao(tabela(*dois[::-1]))


def test_impressao_muda_se_um_valor_muda():
    assert eventos.impressao(tabela(pelo_cdc())) != eventos.impressao(tabela(pelo_cdc(situacao="em_obra")))


def test_ler_devolve_a_versao_lida(configuracao):
    eventos.gravar_sem_duplicar(configuracao, tabela(pelo_cdc("biblioteca")))
    _, primeira = eventos.ler(configuracao)
    eventos.gravar_sem_duplicar(configuracao, tabela(pelo_cdc("quadra")))
    na_fonte, segunda = eventos.ler(configuracao)

    assert segunda > primeira
    assert na_fonte.schema == eventos.ESQUEMA


def conflitos(monkeypatch, quantos: int) -> None:
    """Simula outro escritor confirmando antes, nas primeiras `quantos` tentativas de merge."""
    real = eventos.DeltaTable
    restantes = [quantos]

    class Concorrida(real):
        def merge(self, *argumentos, **nomeados):
            if restantes[0]:
                restantes[0] -= 1
                raise CommitFailedError("conflito simulado com o outro caminho")
            return super().merge(*argumentos, **nomeados)

    monkeypatch.setattr(eventos, "DeltaTable", Concorrida)
    monkeypatch.setattr(eventos.time, "sleep", lambda segundos: None)


def test_merge_que_concorre_com_o_outro_caminho_e_repetido(configuracao, monkeypatch):
    eventos.garantir_tabela(configuracao)
    conflitos(monkeypatch, eventos.TENTATIVAS_DE_GRAVACAO - 1)

    resultado = eventos.gravar_sem_duplicar(configuracao, tabela(pelo_cdc()))

    assert resultado.inseridas == 1


def test_conflito_que_nao_passa_sobe_para_quem_chamou(configuracao, monkeypatch):
    eventos.garantir_tabela(configuracao)
    conflitos(monkeypatch, eventos.TENTATIVAS_DE_GRAVACAO)

    with pytest.raises(CommitFailedError):
        eventos.gravar_sem_duplicar(configuracao, tabela(pelo_cdc()))


def test_controle_das_janelas_fica_fora_da_tabela_delta(configuracao):
    arquivo = janelas.arquivo_de_controle(configuracao)

    assert arquivo.parent == eventos.caminho_da_tabela(configuracao).parent
    assert eventos.caminho_da_tabela(configuracao) not in arquivo.parents


def test_impressao_de_janela_grande_e_exata_em_qualquer_ordem():
    # Com a soma em ponto flutuante, a janela do Censo (3 milhões de linhas) não conferia com ela mesma.
    linhas = [pelo_cdc() | {"vistoria_id": numero} for numero in range(20_000)]
    em_ordem, invertida = tabela(*linhas), tabela(*linhas[::-1])

    assert eventos.impressao(em_ordem) == eventos.impressao(invertida)
    assert not eventos.impressao(em_ordem).endswith("0" * 8)
