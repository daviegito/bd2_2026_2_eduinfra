from pathlib import Path


def imprimir(texto: str, saida: Path | None) -> None:
    print(texto)
    if saida:
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_text(texto + "\n", encoding="utf-8")
