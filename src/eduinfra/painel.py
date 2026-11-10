"""Painel estático de desempenho e infraestrutura a partir da Gold real."""

from html import escape
from pathlib import Path

import duckdb
import plotly.express as px

from eduinfra.configuracao import carregar_configuracao

ITENS_DO_PAINEL = (
    "biblioteca", "lab_informatica", "lab_ciencias", "quadra_esportes",
    "refeitorio", "internet", "acessibilidade_rampas",
)
MINIMO_ESCOLAS = 10
REDES = """case tp_dependencia when 1 then 'Federal' when 2 then 'Estadual'
    when 3 then 'Municipal' when 4 then 'Privada' else 'Outra' end"""


def conectar(configuracao):
    conexao = duckdb.connect()
    try:
        conexao.execute("install postgres; load postgres")
        dsn = configuracao.dsn.replace("'", "''")
        conexao.execute(f"attach '{dsn}' as pg (type postgres, read_only)")
        # As consultas do painel precisam observar o mesmo retrato publicado.
        conexao.execute("begin transaction")
        return conexao
    except Exception:
        conexao.close()
        raise


def preparar_base(conexao, ano):
    exigidas = {
        ("gold", "dim_escola"), ("gold", "dim_item"),
        ("gold", "dim_escola_infraestrutura"), ("gold", "fato_desempenho_escola_ano"),
        ("entrada_transformacao", "enem_escola_ano"),
    }
    presentes = set(conexao.execute("""select table_schema, table_name
        from information_schema.tables where table_catalog = 'pg'""").fetchall())
    if exigidas - presentes:
        raise RuntimeError("Gold completa ausente: execute docker compose run --build --rm transformacao")
    conexao.execute(f"""create temp table base_enem as
        select f.co_entidade, f.ano, f.media_geral, f.data_referencia_infraestrutura,
               e.tp_dependencia, d.escola_infra_sk as perfil, d.itens
        from pg.gold.fato_desempenho_escola_ano f
        join pg.gold.dim_escola e using (co_entidade)
        left join pg.gold.dim_escola_infraestrutura d
          on d.escola_infra_sk = f.escola_infra_sk and d.co_entidade = f.co_entidade
        where f.ano = {int(ano)}""")
    quantidade, unicas = conexao.execute("select count(*), count(distinct co_entidade) from base_enem").fetchone()
    if not quantidade or quantidade != unicas:
        raise RuntimeError(f"Gold ENEM {ano} vazia ou com mais de uma linha por escola; reexecute transformacao")


def ler_cobertura(conexao, ano):
    total, encontradas = conexao.execute("""select count(*), count(c.co_entidade)
        from pg.entrada_transformacao.enem_escola_ano e
        left join pg.gold.dim_escola c using (co_entidade) where e.ano = ?""", [ano]).fetchone()
    na_gold, com_perfil, sem_nota, referencia = conexao.execute("""select count(*), count(perfil),
        count(*) filter (where media_geral is null), min(data_referencia_infraestrutura)
        from base_enem""").fetchone()
    if not total or encontradas != na_gold:
        raise RuntimeError("Gold ENEM difere da cobertura de entrada; reexecute transformacao antes do painel")
    return {
        "escolas_enem": total, "com_censo": encontradas, "sem_censo": total - encontradas,
        "escolas_censo": conexao.execute("select count(*) from pg.gold.dim_escola").fetchone()[0],
        "com_perfil": com_perfil, "sem_nota": sem_nota, "referencia": str(referencia),
    }


def consultar_item(conexao, codigo):
    return conexao.execute(f"""with classificados as (
        select {REDES} as rede, media_geral,
               case json_extract_string(itens, ?)
                   when 'existente' then 'Possui o item'
                   when 'inexistente' then 'Não possui'
                   when 'em_obra' then 'Em obra'
                   when 'inutilizavel' then 'Inutilizável'
                   else 'Não informado' end as infra_status
        from base_enem where media_geral is not null
    )
    select rede, infra_status, count(*) as amostra_n,
           round(avg(media_geral), 1) as media_enem,
           round(stddev_samp(media_geral), 1) as desvio_padrao
    from classificados group by rede, infra_status
    having count(*) >= ? order by rede, infra_status""", [f"$.{codigo}", MINIMO_ESCOLAS]).df()


def montar_graficos(conexao):
    dados = conexao.execute(f"""select {REDES} as rede, count(*) as qtd_escolas
        from base_enem group by rede order by qtd_escolas desc""").df()
    figura = px.bar(dados, x="rede", y="qtd_escolas", color="rede", text="qtd_escolas",
                    title="Escolas na Gold ENEM por rede de ensino",
                    labels={"qtd_escolas": "Número de escolas", "rede": "Rede"})
    figura.update_layout(height=400, showlegend=False)
    # O bundle acompanha a versão de Plotly que gerou os gráficos.
    graficos = ['<div class="chart-card chart-full-width">' +
                figura.to_html(full_html=False, include_plotlyjs="cdn") + '</div>']
    catalogo = dict(conexao.execute("select item_codigo, descricao from pg.gold.dim_item").fetchall())
    selecionados = [codigo for codigo in ITENS_DO_PAINEL if codigo in catalogo]
    if not selecionados:
        raise RuntimeError("Catálogo Gold sem itens do painel; reexecute transformacao")
    for codigo in selecionados:
        dados = consultar_item(conexao, codigo)
        if dados.empty:
            continue
        figura = px.bar(
            dados, x="rede", y="media_enem", color="infra_status", barmode="group",
            error_y="desvio_padrao", text="media_enem", hover_data={"amostra_n": True},
            title=f"Desempenho x {escape(catalogo[codigo])}",
            labels={"media_enem": "Média das escolas", "infra_status": "Situação do item",
                    "rede": "Rede", "amostra_n": "Escolas com nota (n)"},
            color_discrete_map={"Possui o item": "#2ca02c", "Não possui": "#d62728",
                                "Não informado": "#7f7f7f", "Em obra": "#ff7f0e", "Inutilizável": "#9467bd"},
        )
        figura.update_traces(texttemplate="%{text}", textposition="outside")
        figura.update_layout(height=420, legend=dict(orientation="h", y=-0.2))
        graficos.append('<div class="chart-card">' +
                        figura.to_html(full_html=False, include_plotlyjs=False) + '</div>')
    if len(graficos) == 1:
        graficos.append('<p>Nenhum grupo atingiu o mínimo de 10 escolas com nota válida.</p>')
    return graficos


def renderizar_html(cobertura, graficos, ano):
    total_enem = cobertura["escolas_enem"]
    total_censo = cobertura["escolas_censo"]
    juncao_sucesso = cobertura["com_censo"]
    pct_cobertura = 100 * juncao_sucesso / total_enem
    referencia = escape(cobertura["referencia"])
    grid_graficos = "\n".join(graficos)
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Painel EduInfra - Desempenho no ENEM por Infraestrutura</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f0f2f5; color: #333; padding: 20px; }}
        .container {{ max-width: 1300px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
        h1 {{ font-size: 1.6rem; color: #1a252f; margin-bottom: 15px; border-bottom: 2px solid #e9ecef; padding-bottom: 10px; }}
        .alerta {{ background: #fff3cd; color: #856404; border-left: 5px solid #ffeba2; padding: 12px 15px; font-size: 0.95rem; border-radius: 4px; margin-bottom: 20px; line-height: 1.5; }}
        .alerta-demo {{ background: #e2e3e5; color: #383d41; border-left: 5px solid #545b62; }}
        .kpis {{ display: flex; gap: 15px; margin-bottom: 30px; flex-wrap: wrap; }}
        .card {{ flex: 1; min-width: 250px; background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; padding: 15px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.02); }}
        .card .valor {{ font-size: 1.5rem; font-weight: bold; color: #0d6efd; }}
        .card .rotulo {{ font-size: 0.85rem; color: #6c757d; text-transform: uppercase; margin-top: 5px; font-weight: 600; }}
        .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(550px, 1fr)); gap: 25px; }}
        .chart-card {{ background: #ffffff; border: 1px solid #e9ecef; border-radius: 8px; padding: 15px; box-shadow: 0 2px 8px rgba(0,0,0,0.04); width: 100%; }}
        .chart-full-width {{ grid-column: 1 / -1; }}
        .notas-rodape {{ font-size: 0.85rem; color: #6c757d; margin-top: 30px; border-top: 1px solid #e9ecef; padding-top: 15px; line-height: 1.5; }}
        @media (max-width: 768px) {{ .charts-grid {{ grid-template-columns: 1fr; }} .container {{ padding: 15px; }} }}
        .js-plotly-plot, .plot-container {{ width: 100% !important; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>A infraestrutura de uma escola se reflete no desempenho dos alunos no ENEM?</h1>
        <p>Ano do ENEM: <strong>{ano}</strong>. Referência da infraestrutura: <strong>{referencia}</strong>.</p>
        <div class="alerta">
            ⚠️ <strong>Nota Metodológica:</strong> As visualizações abaixo apresentam <u>associação estatística</u>, não causalidade. Os resultados são separados por rede de ensino. Não há controle de INSE, pois ele não está disponível neste contrato. A nota é a média simples das médias escolares válidas, sem ponderação pelo número de participantes. As barras de erro representam <strong>1 Desvio Padrão</strong> das notas entre as escolas (não o intervalo de confiança da média).
        </div>

        <div class="kpis">
            <div class="card">
                <div class="valor" data-indicador="escolas-enem">{total_enem:,}</div>
                <div class="rotulo">Escolas no ENEM</div>
            </div>
            <div class="card">
                <div class="valor">{total_censo:,}</div>
                <div class="rotulo">Escolas no Censo</div>
            </div>
            <div class="card">
                <div class="valor" data-indicador="escolas-com-censo">{juncao_sucesso:,} ({pct_cobertura:.1f}%)</div>
                <div class="rotulo">Cobertura (ENEM ∩ Censo)</div>
            </div>
        </div>

        <p>Sem correspondência no Censo: <strong>{cobertura['sem_censo']:,}</strong>.
        Com perfil na referência do ENEM: <strong>{cobertura['com_perfil']:,}</strong>.
        Escolas sem média geral válida: <strong>{cobertura['sem_nota']:,}</strong>.</p>
        <p>Os totais da junção usam o retrato ENEM de entrada da transformação e a dimensão do Censo.
        Todos os gráficos usam os fatos e dimensões da Gold; ausência de perfil é “Não informado”.</p>
        <div class="charts-grid">
            {grid_graficos}
        </div>

        <div class="notas-rodape">
            * Para evitar a comparação de grupos muito pequenos, <strong>grupos com menos de 10 escolas com média geral válida (n &lt; 10) foram suprimidos</strong> das visualizações de desempenho por item.<br>
            * Você pode inspecionar o <strong>Tamanho da Amostra (n)</strong> passando o mouse sobre qualquer barra.
        </div>
    </div>
</body>
</html>"""



def gerar_painel():
    configuracao = carregar_configuracao()
    with conectar(configuracao) as conexao:
        preparar_base(conexao, configuracao.ano_enem)
        cobertura = ler_cobertura(conexao, configuracao.ano_enem)
        graficos = montar_graficos(conexao)
    html = renderizar_html(cobertura, graficos, configuracao.ano_enem)
    destino = Path("docs/painel.html")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(html, encoding="utf-8")
    print(f"Painel exportado para {destino}")


if __name__ == "__main__":
    gerar_painel()
