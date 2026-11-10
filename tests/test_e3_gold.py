import unittest
import duckdb
from eduinfra.configuracao import carregar_configuracao
import psycopg

class GoldAuditor:
    def __init__(self, configuracao):
        self.config = configuracao
        self.conexao = psycopg.connect(configuracao.dsn, autocommit=True)
        self.conexao.execute("SET search_path TO gold, public")

    def query_count(self, sql: str) -> int:
        return self.conexao.execute(sql).fetchone()[0]

    def query_rows(self, sql: str):
        return self.conexao.execute(sql).fetchall()

class TestE3Gold(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = carregar_configuracao()
        cls.auditor = GoldAuditor(cls.config)

# 1. ESQUEMA
    def test_01_esquema(self):
        colunas = [row[0] for row in self.auditor.query_rows(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'gold' AND table_name = 'fato_vistoria_item'"
        )]
        esperadas = {"vistoria_id", "co_entidade", "item_codigo", "situacao"}
        for col in esperadas:
            self.assertIn(col, colunas, f"Coluna {col} ausente na Gold!")

    # 2. VOLUME (Dinâmico contra a origem)
    def test_02_volume(self):
        total_gold = self.auditor.query_count("SELECT COUNT(*) FROM fato_vistoria_item")

        # Consulta diretamente o schema 'public' do PostgreSQL anexado via DuckDB
        total_origem = self.auditor.query_count("SELECT COUNT(*) FROM pg.public.vistoria_item")

        self.assertEqual(
            total_gold,
            total_origem,
            f"Falha de volume: A Gold tem {total_gold} linhas, mas a origem possui {total_origem}."
        )

    # 3. UNICIDADE (Grão da fato ajustado)
    def test_03_unicidade(self):
        duplicatas = self.auditor.query_count("""
            SELECT COUNT(*) FROM (
                SELECT vistoria_id, item_codigo
                FROM gold.fato_vistoria_item
                GROUP BY 1, 2
                HAVING COUNT(*) > 1
            ) subquery_alias
        """)
        self.assertEqual(duplicatas, 0, f"Encontradas {duplicatas} chaves duplicadas na Gold!")

    # 4. INTEGRIDADE REFERENCIAL (Sem prefixo 'pg.')
    def test_04_integridade_referencial(self):
        faltantes = self.auditor.query_count("""
            SELECT COUNT(DISTINCT g.co_entidade)
            FROM gold.fato_vistoria_item g
            LEFT JOIN gold.dim_escola s ON g.co_entidade = s.co_entidade
            WHERE s.co_entidade IS NULL
        """)
        self.assertEqual(faltantes, 0, f"{faltantes} escolas na fato não existem na dimensão Gold!")

    # 5. DISTRIBUIÇÃO (Sem prefixo 'pg.')
    def test_05_distribuicao(self):
        resultados = self.auditor.query_rows("""
            SELECT s.co_municipio,
                   CAST(SUM(CASE WHEN g.situacao = 'existente' THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as proporcao
            FROM gold.fato_vistoria_item g
            JOIN gold.dim_escola s ON g.co_entidade = s.co_entidade
            GROUP BY s.co_municipio
        """)
        for mun, proporcao in resultados:
            self.assertTrue(0.05 < proporcao < 0.95, f"Distribuição suspeita no Município {mun}: {proporcao:.2%}")

    # 6. CASOS SUJOS DO DOMÍNIO
    def test_06_casos_sujos(self):
        sujos = self.auditor.query_count("""
            SELECT COUNT(*) FROM fato_vistoria_item
            WHERE situacao NOT IN ('existente', 'inexistente', 'nao_informado', 'em_obra', 'inutilizavel')
        """)
        self.assertEqual(sujos, 0, f"DETECTADO: {sujos} registros com situação fora do domínio!")

if __name__ == '__main__':
    unittest.main()
