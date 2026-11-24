"""Prepara os arquivos públicos antes do banco e mede o reaproveitamento dos ZIPs."""

import argparse
import hashlib
import json
from pathlib import Path
import time

from eduinfra.configuracao import Configuracao, carregar_configuracao
from eduinfra.microdados import garantir_microdados, garantir_resultados_enem


def hash_zip(caminho: Path) -> str | None:
    if not caminho.is_file():
        return None
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        while bloco := arquivo.read(4 * 1024**2):
            resumo.update(bloco)
    return resumo.hexdigest()


def preparar(configuracao: Configuracao, saida: Path) -> dict:
    bruto = configuracao.diretorio_dados / "bruto"
    fontes = (
        ("censo", garantir_microdados, bruto / f"censo_escolar_{configuracao.ano_censo}.zip"),
        ("enem", garantir_resultados_enem, bruto / "enem" / f"microdados_enem_{configuracao.ano_enem}.zip"),
    )
    registros = []
    for nome, garantir, pacote in fontes:
        reutilizado = pacote.is_file()
        inicio = time.perf_counter()
        csv = garantir(configuracao)
        registro = {
            "fonte": nome, "zip_reutilizado": reutilizado,
            "segundos_preparo": round(time.perf_counter() - inicio, 3),
            "bytes_zip": pacote.stat().st_size if pacote.is_file() else None,
            "bytes_csv": csv.stat().st_size, "sha256_zip": hash_zip(pacote),
        }
        registros.append(registro)
        print(json.dumps(registro, ensure_ascii=False), flush=True)
    relatorio = {"fontes": registros}
    # Uma entrada inválida precisa falhar antes de permitir o salvamento do cache.
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(relatorio, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return relatorio


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saida", type=Path, required=True)
    opcoes = parser.parse_args()
    preparar(carregar_configuracao(), opcoes.saida)


if __name__ == "__main__":
    main()
