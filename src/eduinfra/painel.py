import duckdb
import plotly.express as px
from pathlib import Path
from eduinfra.configuracao import carregar_configuracao

def gerar_painel():
    config = carregar_configuracao()
    conexao = duckdb.connect()

    # Instala e conecta ao PostgreSQL
    conexao.execute("INSTALL postgres; LOAD postgres;")
    conexao.execute(f"ATTACH '{config.dsn}' AS pg (TYPE POSTGRES);")

    # Mapeia as dimensões e fatos da Gold
    conexao.execute("CREATE VIEW infra AS SELECT * FROM pg.gold.fato_infra_escola_ano;")
    conexao.execute("CREATE VIEW escola AS SELECT * FROM pg.gold.dim_escola;")

    # 1. Identifica dinamicamente as colunas disponíveis na visão 'infra'
    colunas_infra = [
        row[0] for row in conexao.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'infra'"
        ).fetchall()
    ]

    # Busca a coluna de biblioteca ou a primeira coluna de item disponível
    coluna_item = next((c for c in colunas_infra if "biblio" in c.lower()), None)
    if not coluna_item:
        coluna_item = next(
            (c for c in colunas_infra if c.lower() not in ("co_entidade", "ano_censo", "sg_uf", "co_municipio")),
            colunas_infra[-1] if colunas_infra else "co_entidade"
        )

    # 2. Verifica se a tabela fato_enem existe no schema gold
    tabelas_gold = [
        row[0] for row in conexao.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'gold'"
        ).fetchall()
    ]
    tem_enem = "fato_enem" in tabelas_gold

    if tem_enem:
        conexao.execute("CREATE VIEW enem AS SELECT * FROM pg.gold.fato_enem;")
        alerta_dados = ""
    else:
        # Projeção de demonstração com base nas 181 mil escolas da Gold
        conexao.execute("""
            CREATE VIEW enem AS
            SELECT
                co_entidade,
                500 + ABS(HASH(co_entidade) % 200) + (CASE WHEN tp_dependencia = 4 THEN 60 ELSE 0 END) AS nota_media
            FROM pg.gold.dim_escola
            WHERE (HASH(co_entidade) % 10) < 7
        """)
        alerta_dados = """
        <div class="alerta" style="background:#e2e3e5; color:#383d41; border-left-color:#545b62;">
            ℹ️ <strong>Modo de Demonstração:</strong> Tabela <code>gold.fato_enem</code> não detectada.
            Exibindo projeção com base nas escolas da Gold para validação visual do painel.
        </div>
        """

    # 3. Cobertura da Junção ENEM x Censo
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

    # 4. Consulta Principal com a Coluna Encontrada
    df_grafico = conexao.execute(f"""
        SELECT
            CASE
                WHEN e.tp_dependencia = 1 THEN 'Federal'
                WHEN e.tp_dependencia = 2 THEN 'Estadual'
                WHEN e.tp_dependencia = 3 THEN 'Municipal'
                WHEN e.tp_dependencia = 4 THEN 'Privada'
                ELSE 'Outra'
            END AS rede,
            CASE
                WHEN CAST(inf.{coluna_item} AS DOUBLE) > 0 THEN 'Com Item'
                ELSE 'Sem Item'
            END AS infra,
            COUNT(en.co_entidade) AS n_escolas,
            ROUND(AVG(en.nota_media), 1) AS media_enem,
            ROUND(STDDEV(en.nota_media), 1) AS desvio_padrao
        FROM enem en
        JOIN escola e ON en.co_entidade = e.co_entidade
        JOIN infra inf ON en.co_entidade = inf.co_entidade
        GROUP BY 1, 2
        HAVING COUNT(en.co_entidade) >= 10
        ORDER BY rede, infra
    """).df()

    nome_item_exibicao = coluna_item.replace('_', ' ').title()

    # 5. Gráfico Principal (Plotly Bar Chart com Erro e N)
    fig = px.bar(
        df_grafico,
        x="infra",
        y="media_enem",
        color="rede",
        barmode="group",
        error_y="desvio_padrao",
        text="n_escolas",
        title=f"Média no ENEM por Presença de Infraestrutura ({nome_item_exibicao}) — Controlado por Rede",
        labels={"media_enem": "Nota Média no ENEM", "infra": "Infraestrutura", "rede": "Rede de Ensino"}
    )
    fig.update_traces(texttemplate='N=%{text}', textposition='outside')
    fig.update_layout(
        margin=dict(l=20, r=20, t=50, b=20),
        height=420,
        yaxis_range=[300, 850],
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    grafico_html = fig.to_html(full_html=False, include_plotlyjs='cdn')

    # 6. HTML Final
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Painel EduInfra - Infraestrutura vs Desempenho ENEM</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f4f6f8; color: #333; padding: 15px; }}
        .container {{ max-width: 1050px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ font-size: 1.3rem; color: #1a252f; margin-bottom: 8px; }}
        .alerta {{ background: #fff3cd; color: #856404; border-left: 4px solid #ffeba2; padding: 8px 12px; font-size: 0.85rem; border-radius: 4px; margin-bottom: 12px; }}
        .kpis {{ display: flex; gap: 12px; margin-bottom: 12px; }}
        .card {{ flex: 1; background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; padding: 10px; text-align: center; }}
        .card .valor {{ font-size: 1.2rem; font-weight: bold; color: #2c3e50; }}
        .card .rotulo {{ font-size: 0.75rem; color: #6c757d; text-transform: uppercase; margin-top: 2px; }}
        #grafico {{ min-height: 420px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>A infraestrutura de uma escola se reflete no desempenho dos alunos no ENEM?</h1>

        {alerta_dados}

        <div class="alerta">
            ⚠️ <strong>Nota Metodológica:</strong> Esta visualização apresenta <u>associação estatística</u>, não causalidade. Fatores como a rede de ensino e o perfil socioeconômico (INSE) moderam fortemente essa relação.
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
                <div class="valor">{juncao_sucesso:,} ({pct_cobertura:.1f}%)</div>
                <div class="rotulo">Cobertura da Junção (ENEM ∩ Censo)</div>
            </div>
        </div>

        <div id="grafico">
            {grafico_html}
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
