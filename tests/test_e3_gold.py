import unittest
import duckdb
import pytest
from eduinfra.configuracao import carregar_configuracao

class GoldAuditor:
    def __init__(self, configuracao: carregar_configuracao):
        self.config = configuracao
        self.conexao = duckdb.connect()
        self.conexao.execute("INSTALL postgres; LOAD postgres;")

        # Mapeia a tabela Gold do PostgreSQL em vez do Delta Lake
        self.conexao.execute(f"ATTACH '{configuracao.dsn}' AS pg (TYPE POSTGRES);")
        self.conexao.execute("CREATE VIEW fato_vistoria_item AS SELECT * FROM pg.gold.fato_vistoria_item")

    def query_count(self, sql: str) -> int:
        return self.conexao.execute(sql).fetchone()[0]

    def query_rows(self, sql: str):
        return self.conexao.execute(sql).fetchall()

@pytest.mark.integracao
class TestE3Gold(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = carregar_configuracao()
        cls.auditor = GoldAuditor(cls.config)

    # 1. ESQUEMA
    def test_01_esquema(self):
        colunas = [row[0] for row in self.auditor.conexao.execute("DESCRIBE fato_vistoria_item").fetchall()]
        esperadas = {"vistoria_id", "co_entidade", "item_codigo", "situacao"}
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
            SELECT vistoria_id, item_codigo
            FROM fato_vistoria_item
                GROUP BY 1, 2
                HAVING COUNT(*) > 1
            )
        """)
        self.assertEqual(duplicatas, 0, f"Encontradas {duplicatas} chaves duplicadas na Gold!")

    # 4. INTEGRIDADE REFERENCIAL
    def test_04_integridade_referencial(self):
        caminho_escolas = self.config.diretorio_dados / "silver" / f"ano_censo={self.config.ano_censo}" / "escola.parquet"
        if not caminho_escolas.exists():
            self.skipTest("Parquet de escolas não encontrado.")

        faltantes = self.auditor.query_count(f"""
            SELECT COUNT(DISTINCT g.co_entidade)
            FROM fato_vistoria_item g
            LEFT JOIN read_parquet('{caminho_escolas.as_posix()}') s ON g.co_entidade = s.co_entidade
            WHERE s.co_entidade IS NULL
        """)
        self.assertEqual(faltantes, 0, f"{faltantes} escolas na fato não existem na dimensão Silver!")

   # 5. DISTRIBUIÇÃO (Erro Silencioso)
    def test_05_distribuicao(self):
        resultados = self.auditor.query_rows("""
            SELECT s.co_municipio,
                   CAST(SUM(CASE WHEN g.situacao = 'existente' THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as proporcao
            FROM fato_vistoria_item g
            JOIN pg.gold.dim_escola s ON g.co_entidade = s.co_entidade
            GROUP BY s.co_municipio
        """)
        for uf, proporcao in resultados:
            self.assertTrue(0.05 < proporcao < 0.95, f"Distribuição suspeita na UF {uf}: {proporcao:.2%}")

    # 6. CASOS SUJOS DO DOMÍNIO
    def test_06_casos_sujos(self):
        sujos = self.auditor.query_count("""
            SELECT COUNT(*) FROM fato_vistoria_item
            WHERE situacao NOT IN ('existente', 'inexistente', 'nao_informado')
        """)
        self.assertEqual(sujos, 0, f"DETECTADO: {sujos} registros com situação fora do domínio!")

if __name__ == '__main__':
    unittest.main()
