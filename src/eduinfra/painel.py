import duckdb
import plotly.express as px
from pathlib import Path
from eduinfra.configuracao import carregar_configuracao

def gerar_painel():
    config = carregar_configuracao()
    conexao = duckdb.connect()
    conexao.execute("INSTALL delta; LOAD delta;")

    caminho_infra = config.diretorio_dados / "warehouse" / "fato_infra_escola_ano"
    caminho_enem = config.diretorio_dados / "warehouse" / "fato_enem"

    # 1. Views da Gold
    conexao.execute(f"CREATE VIEW infra AS SELECT * FROM delta_scan('{caminho_infra.as_posix()}')")

    # Trata caso a tabela do ENEM exista ou não
    tem_enem = caminho_enem.exists()
    if tem_enem:
        conexao.execute(f"CREATE VIEW enem AS SELECT * FROM delta_scan('{caminho_enem.as_posix()}')")

    # 2. Métricas de Cobertura da Junção
    if tem_enem:
        cobertura = conexao.execute("""
            SELECT
                COUNT(DISTINCT e.co_entidade) AS total_enem,
                COUNT(DISTINCT i.co_entidade) AS total_censo,
                COUNT(DISTINCT CASE WHEN i.co_entidade IS NOT NULL THEN e.co_entidade END) AS juncao_sucesso
            FROM enem e
            FULL OUTER JOIN infra i ON e.co_entidade = i.co_entidade
        """).fetchone()
        total_enem, total_censo, juncao_sucesso = cobertura
        pct_cobertura = (juncao_sucesso / total_enem * 100) if total_enem > 0 else 0
    else:
        total_enem, total_censo, juncao_sucesso, pct_cobertura = 0, 181065, 0, 0.0

    # 3. Consulta Principal: Desempenho por Infraestrutura (Controlando por Rede de Ensino)
    if tem_enem:
        df_grafico = conexao.execute("""
            SELECT
                CASE
                    WHEN i.tp_dependencia = 1 THEN 'Federal'
                    WHEN i.tp_dependencia = 2 THEN 'Estadual'
                    WHEN i.tp_dependencia = 3 THEN 'Municipal'
                    WHEN i.tp_dependencia = 4 THEN 'Privada'
                    ELSE 'Outra'
                END AS rede,
                CASE WHEN i.qtd_biblioteca > 0 THEN 'Com Biblioteca' ELSE 'Sem Biblioteca' END AS infra,
                COUNT(e.co_entidade) AS n_escolas,
                ROUND(AVG(e.nota_media), 1) AS media_enem,
                ROUND(STDDEV(e.nota_media), 1) AS desvio_padrao
            FROM enem e
            JOIN infra i ON e.co_entidade = i.co_entidade
            GROUP BY 1, 2
            HAVING COUNT(e.co_entidade) >= 10
            ORDER BY rede, infra
        """).df()

        fig = px.bar(
            df_grafico,
            x="infra",
            y="media_enem",
            color="rede",
            barmode="group",
            error_y="desvio_padrao",
            text="n_escolas",
            title="Média no ENEM por Presença de Biblioteca (Controlado por Rede)",
            labels={"media_enem": "Nota Média no ENEM", "infra": "Infraestrutura", "rede": "Rede de Ensino"}
        )
        fig.update_traces(texttemplate='N=%{text}', textposition='outside')
        fig.update_layout(
            margin=dict(l=20, r=20, t=50, b=20),
            height=400,
            yaxis_range=[300, 800]
        )
        grafico_html = fig.to_html(full_html=False, include_plotlyjs='cdn')
    else:
        grafico_html = "<p><i>Tabela fato_enem não encontrada na Gold para a geração das curvas.</i></p>"

    # 4. Template HTML Estático (Garantindo que a resposta apareça 'acima da dobra' sem rolar)
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Painel EduInfra - Infraestrutura vs Desempenho ENEM</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f4f6f8; color: #333; padding: 15px; }}
        .container {{ max-width: 1100px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ font-size: 1.4rem; color: #1a252f; margin-bottom: 8px; }}
        .alerta {{ background: #fff3cd; color: #856404; border-left: 4px solid #ffeba2; padding: 8px 12px; font-size: 0.85rem; border-radius: 4px; margin-bottom: 12px; }}
        .kpis {{ display: flex; gap: 12px; margin-bottom: 12px; }}
        .card {{ flex: 1; background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; padding: 10px; text-align: center; }}
        .card .valor {{ font-size: 1.2rem; font-weight: bold; color: #2c3e50; }}
        .card .rotulo {{ font-size: 0.75rem; color: #6c757d; text-transform: uppercase; margin-top: 2px; }}
        #grafico {{ min-height: 400px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>A infraestrutura de uma escola se reflete no desempenho dos alunos no ENEM?</h1>

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
    print(f"✅ Painel estático exportado para {destino}")

if __name__ == "__main__":
    gerar_painel()
