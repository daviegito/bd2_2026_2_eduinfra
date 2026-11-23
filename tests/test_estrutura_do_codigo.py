import ast
from pathlib import Path

import pytest

PACOTE = Path(__file__).resolve().parents[1] / "src" / "eduinfra"

# Mesmo grafo de docs/estrutura-do-codigo.md: um pacote só importa os que vêm antes dele no caminho do dado,
# e nenhum pacote do pipeline importa experimentos.
DEPENDENCIAS_PERMITIDAS = {
    "nucleo": set(),
    "lake": {"nucleo"},
    "origem": {"nucleo", "lake"},
    "ingestao": {"nucleo", "lake", "origem"},
    "cdc": {"nucleo"},
    "gold": {"nucleo", "origem"},
    "consumo": {"nucleo"},
    "experimentos": {"nucleo", "lake", "origem", "ingestao", "cdc", "gold"},
}


def imports_internos(arquivo: Path) -> list[ast.ImportFrom]:
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    return [no for no in ast.walk(arvore) if isinstance(no, ast.ImportFrom) and (no.module or "").startswith("eduinfra.")]


def test_todo_pacote_de_src_tem_regra_de_dependencia():
    pacotes = {pasta.name for pasta in PACOTE.iterdir() if (pasta / "__init__.py").exists()}
    assert pacotes == set(DEPENDENCIAS_PERMITIDAS)


@pytest.mark.parametrize("pacote", sorted(DEPENDENCIAS_PERMITIDAS))
def test_pacote_so_importa_os_pacotes_permitidos(pacote):
    importados = {
        no.module.split(".")[1]
        for arquivo in (PACOTE / pacote).glob("*.py")
        for no in imports_internos(arquivo)
    }
    assert importados - {pacote} <= DEPENDENCIAS_PERMITIDAS[pacote]


def test_nenhum_modulo_importa_nome_privado_de_outro_modulo():
    privados = [
        f"{arquivo.relative_to(PACOTE).as_posix()}: {nome.name}"
        for arquivo in PACOTE.rglob("*.py")
        for no in imports_internos(arquivo)
        for nome in no.names
        if nome.name.startswith("_")
    ]
    assert privados == []
