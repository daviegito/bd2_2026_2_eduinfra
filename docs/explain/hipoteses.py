"""Testa na origem as hipóteses que o plano da view sugeria, antes de decidir pela Gold.

Cada hipótese roda numa transação desfeita, e a remoção do índice bloqueia vistoria_item enquanto
ela dura. Rode em desenvolvimento, na raiz do repositório e com a DATABASE_URL exportada:

    uv run python docs/explain/hipoteses.py > docs/explain/hipoteses.txt
"""

import os
import statistics

import psycopg

from eduinfra.otimizacao_consulta import CONSULTA_NA_VIEW, _explicar_em_json, resumir_execucao

AQUECIMENTOS = 2
REPETICOES = 5

CONSULTA_COM_FILTER = """
select e.tp_dependencia, count(*) filter (where s.situacao = 'inexistente') as escolas
from escola_estado_atual s
join escola e using (co_entidade)
where s.item_codigo = 'biblioteca'
group by e.tp_dependencia
order by e.tp_dependencia
"""

ESTATISTICA_ESTENDIDA = (
    "create statistics vistoria_item_item_situacao (dependencies, mcv) on item_codigo, situacao from vistoria_item",
    "analyze vistoria_item",
)
INDICE_COBRINDO = (
    "create index vistoria_item_cobrindo_por_item on vistoria_item (item_codigo, situacao, vistoria_id)",
    "drop index vistoria_item_por_item",
)

HIPOTESES = {
    "linha de base": (CONSULTA_NA_VIEW, ()),
    "estatística estendida": (CONSULTA_NA_VIEW, ESTATISTICA_ESTENDIDA),
    "work_mem de 64 MB": (CONSULTA_NA_VIEW, ("set local work_mem = '64MB'",)),
    "enable_nestloop desligado": (CONSULTA_NA_VIEW, ("set local enable_nestloop = off",)),
    "filtro no agregado": (CONSULTA_COM_FILTER, ()),
    "índice cobrindo": (CONSULTA_NA_VIEW, INDICE_COBRINDO),
    "filtro no agregado e índice cobrindo": (CONSULTA_COM_FILTER, INDICE_COBRINDO),
}


def linhas_do_join_superior(plano: list) -> tuple[int, int]:
    pendentes = [plano[0]["Plan"]]
    while pendentes:
        no = pendentes.pop(0)
        if "Join Type" in no:
            return no["Plan Rows"], int(no["Actual Rows"])
        pendentes.extend(no.get("Plans", []))
    raise ValueError("o plano não tem join")


def medir(conexao: psycopg.Connection, consulta: str, preparacao: tuple[str, ...]) -> tuple[list, str]:
    for comando in preparacao:
        conexao.execute(comando)
    for _ in range(AQUECIMENTOS):
        conexao.execute(consulta).fetchall()
    plano = "\n".join(linha[0] for linha in conexao.execute(f"explain (analyze, buffers) {consulta}").fetchall())
    serie = [resumir_execucao(_explicar_em_json(conexao, consulta, "analyze, buffers, timing off")) for _ in range(REPETICOES)]
    return serie, plano


def main() -> None:
    planos = {}
    print("| Hipótese | Tempo (mediana) | Em cache | Lidos | Temporários escritos | Linhas no join (estimadas / reais) |")
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conexao:
        conexao.execute("set track_io_timing = on")
        for hipotese, (consulta, preparacao) in HIPOTESES.items():
            with conexao.transaction(force_rollback=True):
                serie, planos[hipotese] = medir(conexao, consulta, preparacao)
                estimadas, reais = linhas_do_join_superior(_explicar_em_json(conexao, consulta, "analyze, timing off"))
            mediana = {
                campo: statistics.median(getattr(execucao, campo) for execucao in serie)
                for campo in ("tempo_ms", "blocos_em_cache", "blocos_lidos", "temporarios_escritos")
            }
            print(
                f"| {hipotese} | {mediana['tempo_ms']:.1f} ms | {mediana['blocos_em_cache']:.0f} "
                f"| {mediana['blocos_lidos']:.0f} | {mediana['temporarios_escritos']:.0f} | {estimadas} / {reais} |"
            )

    for hipotese, plano in planos.items():
        print(f"\n## {hipotese}\n\n{plano}")


if __name__ == "__main__":
    main()
