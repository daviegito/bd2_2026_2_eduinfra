"""Mede o WAL, o tempo e o espaço de uma execução de construir-gold.

Rode só num cluster em que o WAL gerado caiba em max_slot_wal_keep_size, somado ao que o slot já
retém (docs/consulta-otimizada.md). Na raiz do repositório, com a DATABASE_URL exportada:

    uv run python docs/explain/custo_da_gold.py > docs/explain/custo_da_gold.txt
"""

import os
import subprocess
import sys
import time

import psycopg

TAMANHOS = """
select relname, pg_size_pretty(pg_total_relation_size(oid))
from pg_class
where relnamespace = 'gold'::regnamespace and relkind = 'r'
order by pg_total_relation_size(oid) desc
"""


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conexao:
        lsn_inicial = conexao.execute("select pg_current_wal_lsn()").fetchone()[0]
        inicio = time.perf_counter()
        subprocess.run([sys.executable, "-m", "eduinfra.cli", "construir-gold"], check=True, capture_output=True)
        duracao_s = time.perf_counter() - inicio
        wal = conexao.execute(
            "select pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), %s))", (lsn_inicial,)
        ).fetchone()[0]
        total = conexao.execute(
            "select pg_size_pretty(sum(pg_total_relation_size(oid))) from pg_class "
            "where relnamespace = 'gold'::regnamespace and relkind = 'r'"
        ).fetchone()[0]
        tamanhos = conexao.execute(TAMANHOS).fetchall()

    print(f"Duração de construir-gold: {duracao_s:.1f} s")
    print(f"WAL gerado: {wal}")
    print(f"Tamanho total da Gold: {total}")
    for tabela, tamanho in tamanhos:
        print(f"- gold.{tabela}: {tamanho}")


if __name__ == "__main__":
    main()
