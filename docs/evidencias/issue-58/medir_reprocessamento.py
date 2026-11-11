"""Compara recuperação seletiva e carga completa em uma cópia descartável populada.

Insere uma vistoria sintética na origem. Mantém os diretórios de medição para revisão.
"""

import argparse
import json
import os
import shutil
import statistics
import tempfile
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import psycopg

from eduinfra import eventos, fila_de_erros, recuperacao_cdc
from eduinfra.configuracao import carregar_configuracao
from eduinfra.pgoutput import interpretar_lsn
from eduinfra.vistorias_de_teste import inserir_vistoria


def resumo(config):
    with duckdb.connect() as conn:
        conn.execute("load delta")
        colunas = ", ".join(nome for nome in eventos.ESQUEMA.names if nome != "lsn_origem")
        return conn.execute(f"""
            select count(*), count(distinct (vistoria_id, item_codigo)), bit_xor(hash({colunas}))
            from delta_scan(?)
        """, [str(eventos.caminho_da_tabela(config))]).fetchone()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmar-banco-de-teste", action="store_true", required=True)
    parser.add_argument("--repeticoes", type=int, default=3)
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args()
    if args.repeticoes < 1:
        parser.error("--repeticoes deve ser positivo")
    raiz = Path(tempfile.mkdtemp(prefix="eduinfra-medicao-issue58-"))
    base = replace(carregar_configuracao(), diretorio_dados=raiz / "base")
    inicio = time.perf_counter()
    carga = eventos.carregar_da_origem(base)
    preparo_s = time.perf_counter() - inicio
    with psycopg.connect(base.dsn) as conn:
        id = inserir_vistoria(conn, "MEDICAO-58", "Medição descartável da issue 58", datetime.now(timezone.utc) - timedelta(hours=1))
        itens = conn.execute("select item_codigo from vistoria_item where vistoria_id = %s order by item_codigo", (id,)).fetchall()
        plano = conn.execute("explain (analyze, buffers, format json) " + recuperacao_cdc.EVENTO_POR_CHAVE, (id, itens[0][0])).fetchone()[0]
        lsn, agora = conn.execute("select pg_current_wal_lsn()::text, clock_timestamp()").fetchone()
    entradas = [fila_de_erros.montar_linha(interpretar_lsn(lsn), agora, "vistoria_item", "insert", id, item, "KeyError: quantidade") for item, in itens]
    rodadas = []
    for numero in range(args.repeticoes):
        configs = {}
        for modo in ("seletivo", "completo"):
            config = replace(base, diretorio_dados=raiz / f"rodada-{numero + 1}-{modo}")
            # Os arquivos Delta são imutáveis; hardlinks preservam a mesma linha de base.
            shutil.copytree(base.diretorio_dados, config.diretorio_dados, copy_function=os.link)
            fila_de_erros.gravar(config, entradas)
            configs[modo] = config
        medicao = {"rodada": numero + 1}
        # Alterna a ordem para reduzir o favorecimento sistemático pelo cache.
        ordem = ("seletivo", "completo") if numero % 2 == 0 else ("completo", "seletivo")
        for modo in ordem:
            inicio = time.perf_counter()
            if modo == "seletivo":
                resultado = recuperacao_cdc.reprocessar(configs[modo], "operador-01", "esquema_corrigido")
                assert resultado.recuperadas == len(entradas) and resultado.pendentes == 0
                medicao["resultado_seletivo"] = asdict(resultado)
            else:
                resultado = eventos.carregar_da_origem(configs[modo])
                assert resultado.inseridas == len(entradas)
                medicao["linhas_lidas_completo"] = resultado.recebidas
            medicao[f"{modo}_s"] = time.perf_counter() - inicio
        seletivo, completo = resumo(configs["seletivo"]), resumo(configs["completo"])
        assert seletivo == completo and seletivo[0] == seletivo[1]
        medicao["resumo_destinos"] = seletivo
        repeticao = recuperacao_cdc.reprocessar(configs["seletivo"], "operador-01", "esquema_corrigido")
        assert repeticao.recuperadas == 0 and repeticao.linhas_lidas == 0
        medicao["resultado_repeticao"] = asdict(repeticao)
        rodadas.append(medicao)
        print(json.dumps(medicao), flush=True)
    resultado = {"base_eventos": carga.recebidas, "itens_na_fila": len(entradas),
                 "preparo_s": preparo_s, "diretorio_preservado": str(raiz), "rodadas": rodadas,
                 "mediana_seletivo_s": statistics.median(r["seletivo_s"] for r in rodadas),
                 "mediana_completo_s": statistics.median(r["completo_s"] for r in rodadas),
                 "plano_por_chave": plano}
    args.saida.write_text(json.dumps(resultado, indent=2) + "\n")


if __name__ == "__main__":
    main()
