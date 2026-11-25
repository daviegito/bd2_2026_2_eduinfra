from pathlib import Path

import pytest

from scripts.verificar_caminhos import verificar

LINK_MAIN = "https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/"
LINK_FIXADO = "https://github.com/daviegito/bd2_2026_2_eduinfra/blob/eaefafe/"


@pytest.fixture
def repositorio(tmp_path: Path) -> Path:
    (tmp_path / "src" / "eduinfra" / "cdc").mkdir(parents=True)
    (tmp_path / "src" / "eduinfra" / "cdc" / "eventos.py").touch()
    (tmp_path / "src" / "eduinfra" / "medicoes").mkdir()
    (tmp_path / "src" / "eduinfra" / "medicoes" / "comparacao_latencia.py").touch()
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    return tmp_path


def escrever(repositorio: Path, nome: str, texto: str) -> None:
    (repositorio / nome).write_text(texto, encoding="utf-8")


def caminhos_acusados(repositorio: Path) -> list[str]:
    return [problema.caminho for problema in verificar(repositorio)]


@pytest.mark.parametrize(
    "texto",
    [
        "Veja `src/eduinfra/cdc/eventos.py`.",
        f"[eventos]({LINK_MAIN}src/eduinfra/cdc/eventos.py)",
        f"[antigo]({LINK_FIXADO}src/eduinfra/eventos.py)",
        "Medições em `src/eduinfra/medicoes/comparacao_*.py`.",
        "O pacote `src/eduinfra/cdc/` guarda o CDC.",
    ],
)
def test_caminho_que_existe_ou_link_fixado_em_commit_nao_e_acusado(repositorio, texto):
    escrever(repositorio, "README.md", texto)
    assert caminhos_acusados(repositorio) == []


@pytest.mark.parametrize(
    ("texto", "acusado"),
    [
        ("Veja `src/eduinfra/eventos.py`.", "src/eduinfra/eventos.py"),
        (f"[eventos]({LINK_MAIN}src/eduinfra/eventos.py)", "src/eduinfra/eventos.py"),
        ("Rode `tests/test_eventos.py`.", "tests/test_eventos.py"),
    ],
)
def test_caminho_inexistente_e_acusado(repositorio, texto, acusado):
    escrever(repositorio, "README.md", texto)
    assert caminhos_acusados(repositorio) == [acusado]


def test_link_relativo_e_resolvido_a_partir_da_pasta_do_documento(repositorio):
    escrever(repositorio, "docs/adr/0008.md", "[ok](../../src/eduinfra/cdc/eventos.py) [quebrado](../src/eduinfra/cdc/eventos.py)")
    assert caminhos_acusados(repositorio) == ["../src/eduinfra/cdc/eventos.py"]


def test_registro_de_uso_de_ia_e_diario_ficam_fora_da_verificacao(repositorio):
    (repositorio / "docs" / "diario").mkdir()
    escrever(repositorio, "AI-USAGE.md", "`src/eduinfra/eventos.py`")
    escrever(repositorio, "docs/diario/semana-10.md", "`src/eduinfra/eventos.py`")
    assert caminhos_acusados(repositorio) == []


def test_acusacao_informa_documento_e_linha(repositorio):
    escrever(repositorio, "README.md", "linha um\n`src/eduinfra/eventos.py`\n")
    assert [str(problema) for problema in verificar(repositorio)] == ["README.md:2: src/eduinfra/eventos.py"]
