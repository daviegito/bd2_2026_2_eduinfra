import duckdb
import plotly.express as px
from pathlib import Path
from eduinfra.configuracao import carregar_configuracao

def formatar_nome_item(coluna: str) -> str:
    """Transforma o nome técnico da coluna em um título legível."""
    substituicoes = {"qtd_": "", "in_": "", "st_": "", "_": " "}
    nome = coluna.lower()
    for chave, valor in substituicoes.items():
        nome = nome.replace(chave, valor)

    ajustes = {
        "internet": "Acesso à Internet",
        "lab informatica": "Lab. de Informática",
        "lab ciencias": "Lab. de Ciências",
        "quadra": "Quadra de Esportes",
        "refeitorio": "Refeitório"
    }
    for chave, valor in ajustes.items():
        if chave in nome:
            return valor

    return nome.strip().title()

def gerar_painel():
    config = carregar_configuracao()
    conexao = duckdb.connect()

    conexao.execute("INSTALL postgres; LOAD postgres;")
    conexao.execute(f"ATTACH '{config.dsn}' AS pg (TYPE POSTGRES);")

    # 1. Base da Infraestrutura (Usando a Fato direta)
    conexao.execute("CREATE VIEW infra AS SELECT * FROM pg.gold.fato_infra_escola_ano;")
    conexao.execute("CREATE VIEW escola AS SELECT * FROM pg.gold.dim_escola;")

    colunas_infra = [row[0] for row in conexao.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'infra'").fetchall()]
    palavras_chave_relevantes = ['biblioteca', 'refeitorio', 'quadra', 'lab_informatica', 'lab_ciencias', 'acessibilidade', 'rampa', 'internet']

    cols_itens = []
    for c in colunas_infra:
        for p in palavras_chave_relevantes:
            if p in c.lower() and c not in cols_itens:
                cols_itens.append(c)

    # 2. Prepara a Tabela do ENEM
    tabelas_gold = [row[0] for row in conexao.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'gold'").fetchall()]
    tem_enem = "fato_enem" in tabelas_gold

    if not tem_enem:
        # Aborta a execução em vez de gerar dados falsos (Ponto Crítico do Review)
        print("⚠️ Tabela gold.fato_enem não detectada. Execução abortada para evitar publicação de dados falsos em produção.")
        return

    conexao.execute("CREATE VIEW enem AS SELECT * FROM pg.gold.fato_enem;")
    alerta_dados = ""

    # 3. Métricas Principais (Agrupando pela chave substituta temporal)
    cobertura = conexao.execute("""
        SELECT
            COUNT(DISTINCT e.escola_infra_sk) AS total_enem,
            (SELECT COUNT(DISTINCT escola_infra_sk) FROM infra) AS total_censo,
            COUNT(DISTINCT CASE WHEN i.escola_infra_sk IS NOT NULL THEN e.escola_infra_sk END) AS juncao_sucesso
        FROM enem e
        LEFT JOIN infra i ON e.escola_infra_sk = i.escola_infra_sk
    """).fetchone()

    total_enem, total_censo, juncao_sucesso = cobertura
    pct_cobertura = (juncao_sucesso / total_enem * 100) if total_enem > 0 else 0

    graficos_html = []

    # =========================================================
    # GRÁFICO 1: Espaço Amostral Base (Número de Escolas)
    # =========================================================
    df_amostra = conexao.execute("""
        SELECT
            CASE
                WHEN e.tp_dependencia = 1 THEN 'Federal'
                WHEN e.tp_dependencia = 2 THEN 'Estadual'
                WHEN e.tp_dependencia = 3 THEN 'Municipal'
                WHEN e.tp_dependencia = 4 THEN 'Privada'
                ELSE 'Outra'
            END AS rede,
            COUNT(DISTINCT inf.co_entidade) AS qtd_escolas
        FROM enem en
        JOIN infra inf ON en.escola_infra_sk = inf.escola_infra_sk
        JOIN escola e ON inf.co_entidade = e.co_entidade
        GROUP BY 1
        ORDER BY qtd_escolas DESC
    """).df()

    fig_amostra = px.bar(
        df_amostra, x="rede", y="qtd_escolas", color="rede",
        text="qtd_escolas", title="Espaço Amostral: Volume de Escolas no ENEM por Rede de Ensino",
        labels={"qtd_escolas": "Número de Escolas", "rede": "Rede"}
    )
    fig_amostra.update_traces(texttemplate='%{text:,.0f}', textposition='outside')
    fig_amostra.update_layout(margin=dict(l=15, r=15, t=50, b=15), height=400, showlegend=False, yaxis_title="Total de Escolas")
    graficos_html.append(f'<div class="chart-card chart-full-width">{fig_amostra.to_html(full_html=False, include_plotlyjs=False)}</div>')

    # =========================================================
    # GRÁFICOS SEGUINTES: Desempenho por Item Relevante
    # =========================================================
    for item_col in cols_itens:
        nome_exibicao = formatar_nome_item(item_col)

        df_item = conexao.execute(f"""
            SELECT
                CASE
                    WHEN e.tp_dependencia = 1 THEN 'Federal'
                    WHEN e.tp_dependencia = 2 THEN 'Estadual'
                    WHEN e.tp_dependencia = 3 THEN 'Municipal'
                    WHEN e.tp_dependencia = 4 THEN 'Privada'
                    ELSE 'Outra'
                END AS rede,
                CASE
                    WHEN inf.{item_col} IS NULL THEN 'Não informado'
                    WHEN CAST(inf.{item_col} AS DOUBLE) > 0 THEN 'Possui o item'
                    ELSE 'Não possui'
                END AS infra_status,
                COUNT(en.escola_infra_sk) AS amostra_n,
                ROUND(AVG(en.nota_media), 1) AS media_enem,
                ROUND(STDDEV(en.nota_media), 1) AS desvio_padrao
            FROM enem en
            JOIN infra inf ON en.escola_infra_sk = inf.escola_infra_sk
            JOIN escola e ON inf.co_entidade = e.co_entidade
            GROUP BY 1, 2
            HAVING COUNT(en.escola_infra_sk) >= 10
            ORDER BY rede, infra_status
        """).df()

        if not df_item.empty:
            fig = px.bar(
                df_item, x="rede", y="media_enem", color="infra_status", barmode="group",
                error_y="desvio_padrao", text="media_enem",
                hover_data={"amostra_n": True},
                title=f"Desempenho x {nome_exibicao}",
                labels={"media_enem": "Nota Média", "infra_status": "Status do Item", "rede": "Rede", "amostra_n": "Tamanho da Amostra (n)"},
                color_discrete_map={"Possui o item": "#2ca02c", "Não possui": "#d62728", "Não informado": "#7f7f7f"}
            )
            fig.update_traces(texttemplate='%{text}', textposition='outside')
            fig.update_layout(
                margin=dict(l=15, r=15, t=50, b=50), height=420, yaxis_range=[300, 850],
                legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5)
            )
            graficos_html.append(f'<div class="chart-card">{fig.to_html(full_html=False, include_plotlyjs=False)}</div>')

    grid_graficos = "\n".join(graficos_html)

    # =========================================================
    # HTML FINAL E CSS RESPONSIVO
    # =========================================================
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Painel EduInfra - Desempenho no ENEM por Infraestrutura</title>
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
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
        {alerta_dados}
        <div class="alerta">
            ⚠️ <strong>Nota Metodológica:</strong> As visualizações abaixo apresentam <u>associação estatística</u>, não causalidade. Fatores como rede de ensino e perfil socioeconômico moderam fortemente essas relações. As barras de erro representam <strong>1 Desvio Padrão</strong> das notas entre as escolas (não o intervalo de confiança da média).
        </div>

        <div class="kpis">
            <div class="card">
                <div class="valor">{total_enem:,}</div>
                <div class="rotulo">Escolas no ENEM</div>
            </div>
            <div class="card">
                <div class="valor">{total_censo:,}</div>
                <div class="rotulo">Retratos de Infraestrutura</div>
            </div>
            <div class="card">
                <div class="valor">{juncao_sucesso:,} ({(pct_cobertura):.1f}%)</div>
                <div class="rotulo">Cobertura (ENEM ∩ Censo)</div>
            </div>
        </div>

        <div class="charts-grid">
            {grid_graficos}
        </div>

        <div class="notas-rodape">
            * Para preservar a representatividade estatística e a privacidade dos dados, <strong>grupos com menos de 10 escolas (n < 10) foram suprimidos</strong> das visualizações de desempenho por item.<br>
            * Você pode inspecionar o <strong>Tamanho da Amostra (n)</strong> passando o mouse sobre qualquer barra.
        </div>
    </div>
</body>
</html>"""

    destino = Path("docs/painel.html")
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(html, encoding="utf-8")
    print(f"✅ Painel estático exportado com sucesso para {destino}")

if __name__ == "__main__":
    gerar_painel()
