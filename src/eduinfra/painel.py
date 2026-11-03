import duckdb
import plotly.express as px
from pathlib import Path
from eduinfra.configuracao import carregar_configuracao

def formatar_nome_item(coluna: str) -> str:
    """Transforma o nome técnico da coluna em um título legível."""
    substituicoes = {
        "qtd_": "",
        "in_": "",
        "st_": "",
        "_": " "
    }
    nome = coluna.lower()
    for chave, valor in substituicoes.items():
        nome = nome.replace(chave, valor)

    # Ajustes finos manuais para itens específicos do Censo
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

    # Conecta ao PostgreSQL via DuckDB
    conexao.execute("INSTALL postgres; LOAD postgres;")
    conexao.execute(f"ATTACH '{config.dsn}' AS pg (TYPE POSTGRES);")

    conexao.execute("CREATE VIEW infra AS SELECT * FROM pg.gold.fato_infra_escola_ano;")
    conexao.execute("CREATE VIEW escola AS SELECT * FROM pg.gold.dim_escola;")

    # 1. Definimos manualmente apenas os itens estruturais relevantes
    colunas_infra = [row[0] for row in conexao.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'infra'").fetchall()]

    # Lista de palavras-chave para filtrar apenas as colunas estruturais reais (exclui pacotes de rede/sinal)
    palavras_chave_relevantes = ['biblioteca', 'refeitorio', 'quadra', 'lab_informatica', 'lab_ciencias', 'acessibilidade', 'rampa', 'internet']

    cols_itens = []
    for c in colunas_infra:
        for p in palavras_chave_relevantes:
            if p in c.lower() and c not in cols_itens:
                cols_itens.append(c)

    # 2. Prepara a Tabela do ENEM (Real ou Simulação)
    tabelas_gold = [row[0] for row in conexao.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'gold'").fetchall()]
    tem_enem = "fato_enem" in tabelas_gold

    if tem_enem:
        conexao.execute("CREATE VIEW enem AS SELECT * FROM pg.gold.fato_enem;")
        alerta_dados = ""
    else:
        conexao.execute("""
            CREATE VIEW enem AS
            SELECT
                co_entidade,
                500 + ABS(HASH(co_entidade) % 180) + (CASE WHEN tp_dependencia = 4 THEN 70 ELSE 0 END) AS nota_media
            FROM pg.gold.dim_escola
            WHERE (HASH(co_entidade) % 10) < 7
        """)
        alerta_dados = """
        <div class="alerta">
            ℹ️ <strong>Modo de Demonstração:</strong> Tabela <code>gold.fato_enem</code> não detectada.
            Exibindo projeção com base nas escolas da Gold para validação de layout do painel.
        </div>
        """

    # 3. Métricas Principais
    cobertura = conexao.execute("""
        SELECT
            COUNT(DISTINCT e.co_entidade) AS total_enem,
            (SELECT COUNT(DISTINCT co_entidade) FROM infra) AS total_censo,
            COUNT(DISTINCT CASE WHEN i.co_entidade IS NOT NULL THEN e.co_entidade END) AS juncao_sucesso
        FROM enem e
        LEFT JOIN infra i ON e.co_entidade = i.co_entidade
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
            COUNT(en.co_entidade) AS qtd_escolas
        FROM enem en
        JOIN escola e ON en.co_entidade = e.co_entidade
        GROUP BY 1
        ORDER BY qtd_escolas DESC
    """).df()

    fig_amostra = px.bar(
        df_amostra, x="rede", y="qtd_escolas", color="rede",
        text="qtd_escolas", title="Espaço Amostral: Volume de Escolas no ENEM por Rede de Ensino",
        labels={"qtd_escolas": "Número de Escolas", "rede": "Rede"}
    )
    fig_amostra.update_traces(texttemplate='%{text:,.0f}', textposition='outside')
    fig_amostra.update_layout(
        margin=dict(l=15, r=15, t=50, b=15), height=400, showlegend=False,
        yaxis_title="Total de Escolas"
    )
    graficos_html.append(f'<div class="chart-card chart-full-width">{fig_amostra.to_html(full_html=False, include_plotlyjs=False)}</div>')

    # =========================================================
    # GRÁFICOS SEGUINTES: Desempenho por cada Item Relevante
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
                    WHEN CAST(inf.{item_col} AS DOUBLE) > 0 THEN 'Possui o item'
                    ELSE 'Não possui'
                END AS infra_status,
                COUNT(en.co_entidade) AS amostra_n,
                ROUND(AVG(en.nota_media), 1) AS media_enem,
                ROUND(STDDEV(en.nota_media), 1) AS desvio_padrao
            FROM enem en
            JOIN escola e ON en.co_entidade = e.co_entidade
            JOIN infra inf ON en.co_entidade = inf.co_entidade
            GROUP BY 1, 2
            HAVING COUNT(en.co_entidade) >= 10
            ORDER BY rede, infra_status
        """).df()

        if not df_item.empty:
            # Não exibe mais o N nas barras, exibe apenas a nota média
            fig = px.bar(
                df_item,
                x="rede",
                y="media_enem",
                color="infra_status",
                barmode="group",
                error_y="desvio_padrao",
                text="media_enem",
                title=f"Desempenho x {nome_exibicao}",
                labels={"media_enem": "Nota Média", "infra_status": "Infraestrutura", "rede": "Rede"}
            )
            fig.update_traces(texttemplate='%{text}', textposition='outside')

            # Legenda melhor espaçada na parte de baixo do gráfico
            fig.update_layout(
                margin=dict(l=15, r=15, t=50, b=50),
                height=420,
                yaxis_range=[300, 850],
                legend=dict(
                    orientation="h",
                    yanchor="top", y=-0.15,
                    xanchor="center", x=0.5
                )
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
        h2 {{ font-size: 1.3rem; color: #2c3e50; margin: 30px 0 15px 0; }}

        .alerta {{ background: #fff3cd; color: #856404; border-left: 5px solid #ffeba2; padding: 12px 15px; font-size: 0.95rem; border-radius: 4px; margin-bottom: 20px; line-height: 1.5; }}
        .alerta-demo {{ background: #e2e3e5; color: #383d41; border-left: 5px solid #545b62; }}

        .kpis {{ display: flex; gap: 15px; margin-bottom: 30px; flex-wrap: wrap; }}
        .card {{ flex: 1; min-width: 250px; background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; padding: 15px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.02); }}
        .card .valor {{ font-size: 1.5rem; font-weight: bold; color: #0d6efd; }}
        .card .rotulo {{ font-size: 0.85rem; color: #6c757d; text-transform: uppercase; margin-top: 5px; font-weight: 600; }}

        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(550px, 1fr));
            gap: 25px;
        }}
        .chart-card {{
            background: #ffffff;
            border: 1px solid #e9ecef;
            border-radius: 8px;
            padding: 15px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.04);
            width: 100%;
        }}
        .chart-full-width {{
            grid-column: 1 / -1;
        }}

        /* Ajuste para telas menores */
        @media (max-width: 768px) {{
            .charts-grid {{ grid-template-columns: 1fr; }}
            .container {{ padding: 15px; }}
        }}
        /* Garante que o plotly preencha 100% da div */
        .js-plotly-plot, .plot-container {{ width: 100% !important; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>A infraestrutura de uma escola se reflete no desempenho dos alunos no ENEM?</h1>

        {alerta_dados}

        <div class="alerta">
            ⚠️ <strong>Nota Metodológica:</strong> As visualizações abaixo apresentam <u>associação estatística</u>, não causalidade. Fatores como a rede de ensino e o perfil socioeconômico (INSE) moderam fortemente essas relações, por isso os gráficos controlam os resultados por Rede. As barras de erro representam 1 Desvio Padrão.
        </div>

        <div class="kpis">
            <div class="card">
                <div class="valor">{total_enem:,}</div>
                <div class="rotulo">Escolas no ENEM</div>
            </div>
            <div class="card">
                <div class="valor">{total_censo:,}</div>
                <div class="rotulo">Escolas no Censo</div>
            </div>
            <div class="card">
                <div class="valor">{juncao_sucesso:,} ({(pct_cobertura):.1f}%)</div>
                <div class="rotulo">Cobertura da Junção (ENEM ∩ Censo)</div>
            </div>
        </div>

        <div class="charts-grid">
            {grid_graficos}
        </div>
    </div>
</body>
</html>"""

    destino = Path("docs/index.html")
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(html, encoding="utf-8")
    print(f"✅ Painel estático exportado com sucesso para {destino}")

if __name__ == "__main__":
    gerar_painel()
