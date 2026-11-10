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
        colunas = [row[0] for row in self.auditor.conexao.execute("DESCRIBE fato_vistoria_item").fetchall()]
        esperadas = {"ano_censo", "sg_uf", "co_entidade", "item_codigo", "situacao"}
        for col in esperadas:
            self.assertIn(col, colunas, f"Coluna {col} ausente na Gold!")

    # 2. VOLUME (Próximo aos 3.08M itens)
    def test_02_volume(self):
        total = self.auditor.query_count("SELECT COUNT(*) FROM fato_vistoria_item")
        self.assertTrue(3_000_000 <= total <= 3_200_000, f"Volume inesperado na fato: {total} registros")

    # 3. UNICIDADE (Grão da fato)
    def test_03_unicidade(self):
        duplicatas = self.auditor.query_count("""
            SELECT COUNT(*) FROM (
                SELECT ano_censo, sg_uf, co_entidade, item_codigo
                FROM fato_vistoria_item
                GROUP BY 1, 2, 3, 4
                HAVING COUNT(*) > 1
            )
        """)
        self.assertEqual(duplicatas, 0, f"Encontradas {duplicatas} chaves duplicadas na Gold!")

    # 4. INTEGRIDADE REFERENCIAL
    def test_04_integridade_referencial(self):
        faltantes = self.auditor.query_count("""
            SELECT COUNT(DISTINCT g.co_entidade)
            FROM fato_vistoria_item g
            LEFT JOIN pg.gold.dim_escola s ON g.co_entidade = s.co_entidade
            WHERE s.co_entidade IS NULL
        """)
        self.assertEqual(faltantes, 0, f"{faltantes} escolas na fato não existem na dimensão Gold!")

   # 5. DISTRIBUIÇÃO (Erro Silencioso)
    def test_05_distribuicao(self):
        resultados = self.auditor.query_rows("""
            SELECT s.co_municipio,
                   CAST(SUM(CASE WHEN g.situacao = 'existente' THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as proporcao
            FROM fato_vistoria_item g
            JOIN pg.gold.dim_escola s ON g.co_entidade = s.co_entidade
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
