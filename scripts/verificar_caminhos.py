"""Falha se um documento citar um caminho do repositório que não existe (issue #97)."""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORIO = "github.com/daviegito/bd2_2026_2_eduinfra"

# Registros da época (AI-USAGE, diário e evidências de medição): citam os caminhos como eram, e a tabela
# de docs/estrutura-do-codigo.md cobre a leitura.
IGNORADOS = ("AI-USAGE.md", "docs/diario/", "docs/evidencias/", ".venv/", "site/", "node_modules/")

CAMINHO_LOCAL = re.compile(r"(?<![\w/.-])(?P<subida>(?:\.\./)*)(?P<caminho>(?:src|tests|scripts)/[\w./*-]*[\w/*])")
# Link fixado em commit continua válido depois de uma mudança de caminho; só o que aponta para main é conferido.
LINK_PARA_MAIN = re.compile(re.escape(REPOSITORIO) + r"/(?:blob|tree)/main/(?P<caminho>[^)\s#?\"'>]+)")


@dataclass(frozen=True)
class CaminhoInexistente:
    documento: Path
    linha: int
    caminho: str

    def __str__(self) -> str:
        return f"{self.documento.as_posix()}:{self.linha}: {self.caminho}"


def existe(raiz: Path, caminho: Path) -> bool:
    relativo = caminho.relative_to(raiz) if caminho.is_absolute() else caminho
    if "*" in relativo.as_posix():
        return any(raiz.glob(relativo.as_posix()))
    return (raiz / relativo).exists()


def citacoes(texto: str) -> list[tuple[int, str, str]]:
    encontradas = []
    for numero, linha in enumerate(texto.splitlines(), start=1):
        for casamento in LINK_PARA_MAIN.finditer(linha):
            encontradas.append((numero, "", casamento.group("caminho")))
        sem_links_externos = re.sub(r"https?://\S+", "", linha)
        for casamento in CAMINHO_LOCAL.finditer(sem_links_externos):
            encontradas.append((numero, casamento.group("subida"), casamento.group("caminho")))
    return encontradas


def verificar_documento(raiz: Path, documento: Path) -> list[CaminhoInexistente]:
    problemas = []
    texto = documento.read_text(encoding="utf-8")
    for linha, subida, caminho in citacoes(texto):
        alvo = (documento.parent / subida / caminho).resolve() if subida else raiz / caminho
        if not existe(raiz, alvo.resolve()):
            problemas.append(CaminhoInexistente(documento.relative_to(raiz), linha, subida + caminho))
    return problemas


def documentos(raiz: Path) -> list[Path]:
    todos = sorted(raiz.rglob("*.md"))
    return [doc for doc in todos if not doc.relative_to(raiz).as_posix().startswith(IGNORADOS)]


def verificar(raiz: Path) -> list[CaminhoInexistente]:
    raiz = raiz.resolve()
    return [problema for doc in documentos(raiz) for problema in verificar_documento(raiz, doc)]


def main() -> int:
    raiz = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    problemas = verificar(raiz)
    for problema in problemas:
        print(f"caminho inexistente em {problema}")
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
