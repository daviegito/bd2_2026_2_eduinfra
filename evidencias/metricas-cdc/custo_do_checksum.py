"""Custo de comparar por hash a origem e o Delta, só com leitura (ADR 0007). Roda no contêiner, porque usa resource."""

import os
import resource
import time
from pathlib import Path

import duckdb
from deltalake import DeltaTable

CAMINHO = Path(os.environ["DIRETORIO_DADOS"]) / "warehouse" / "eventos_vistoria"

ORIGEM = """
select v.id::bigint as vistoria_id, i.item_codigo, v.co_entidade::int as co_entidade, m.sg_uf,
       v.origem, v.ocorrido_em, v.registrado_em, i.situacao::text as situacao,
       i.quantidade::int as quantidade,
       strftime(v.ocorrido_em at time zone 'UTC', '%Y-%m') as mes_ocorrencia
from origem.public.vistoria v
join origem.public.vistoria_item i on i.vistoria_id = v.id
join origem.public.escola e on e.co_entidade = v.co_entidade
join origem.public.municipio m on m.co_municipio = e.co_municipio
"""

CANONICO = (
    "md5_number(concat_ws('|', vistoria_id, item_codigo, co_entidade, sg_uf, origem, "
    "epoch_us(ocorrido_em), epoch_us(registrado_em), situacao, coalesce(quantidade::varchar, 'nulo')))"
)


def medir(rotulo, conexao, sql):
    inicio = time.perf_counter()
    linhas = conexao.execute(sql).fetchall()
    print(f"{rotulo}: {time.perf_counter() - inicio:.1f} s, pico de memória do processo {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MiB")
    return linhas


def main() -> None:
    tabela = DeltaTable(str(CAMINHO))
    print("versão do Delta:", tabela.version())
    print("arquivos no total:", len(tabela.file_uris()))

    conexao = duckdb.connect()
    conexao.execute("install postgres; load postgres; install delta; load delta;")
    conexao.execute("set memory_limit = '1GB'")
    dsn = os.environ["DATABASE_URL"].replace("'", "''")
    conexao.execute(f"attach '{dsn}' as origem (type postgres, read_only)")
    destino = f"delta_scan('{CAMINHO.as_posix()}')"

    no_destino = medir(
        "impressão por partição, Delta",
        conexao,
        f"select mes_ocorrencia, lsn_origem is null as linha_de_base, count(*) as linhas, "
        f"bit_xor(({CANONICO}) >> 64)::varchar as impressao from {destino} group by 1, 2 order by 1, 2",
    )
    for linha in no_destino:
        print("  ", linha)

    na_origem = medir(
        "impressão por partição, origem",
        conexao,
        f"select mes_ocorrencia, count(*) as linhas, bit_xor(({CANONICO}) >> 64)::varchar as impressao "
        f"from ({ORIGEM}) group by 1 order by 1",
    )
    for linha in na_origem:
        print("  ", linha)

    if os.environ.get("LINHA_A_LINHA"):
        comparado = medir(
            "comparação linha a linha, tabela inteira",
            conexao,
            f"""
            with o as (select vistoria_id, item_codigo, {CANONICO} as h from ({ORIGEM})),
                 d as (select vistoria_id, item_codigo, {CANONICO} as h from {destino})
            select count(*) filter (where o.h = d.h) as iguais,
                   count(*) filter (where o.h <> d.h) as diferentes,
                   count(*) filter (where d.h is null) as so_na_origem,
                   count(*) filter (where o.h is null) as so_no_delta
            from o full join d using (vistoria_id, item_codigo)
            """,
        )
        print("  iguais, diferentes, só na origem, só no Delta:", comparado)


if __name__ == "__main__":
    main()
