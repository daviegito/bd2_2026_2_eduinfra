import importlib.util
import unittest
from eduinfra.configuracao import carregar_configuracao
import psycopg

if importlib.util.find_spec("pytest") is not None:
    import pytest

    pytestmark = pytest.mark.integracao


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

    def test_01_esquema(self):
        colunas_escola = {
            row[0]
            for row in self.auditor.query_rows(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'gold' AND table_name = 'dim_escola'"
            )
        }
        for col in ("co_entidade", "co_municipio", "no_entidade", "tp_dependencia"):
            self.assertIn(col, colunas_escola, f"Coluna {col} ausente em gold.dim_escola!")

        colunas_fato = {
            row[0]
            for row in self.auditor.query_rows(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'gold' AND table_name = 'fato_infra_escola_ano'"
            )
        }
        self.assertIn("co_entidade", colunas_fato, "Coluna co_entidade ausente em gold.fato_infra_escola_ano!")

    def test_02_volume(self):
        total_escolas = self.auditor.query_count("SELECT COUNT(*) FROM gold.dim_escola")
        total_fato = self.auditor.query_count("SELECT COUNT(*) FROM gold.fato_infra_escola_ano")
        self.assertGreater(total_escolas, 0, "gold.dim_escola está vazia!")
        self.assertGreater(total_fato, 0, "gold.fato_infra_escola_ano está vazia!")

    def test_03_unicidade(self):
        duplicatas = self.auditor.query_count("""
            SELECT COUNT(*) FROM (
                SELECT co_entidade
                FROM gold.dim_escola
                GROUP BY 1
                HAVING COUNT(*) > 1
            ) sub
        """)
        self.assertEqual(duplicatas, 0, f"Encontradas {duplicatas} escolas duplicadas em gold.dim_escola!")

    def test_04_integridade_referencial(self):
        faltantes_escola = self.auditor.query_count("""
            SELECT COUNT(DISTINCT f.co_entidade)
            FROM gold.fato_infra_escola_ano f
            LEFT JOIN gold.dim_escola s ON f.co_entidade = s.co_entidade
            WHERE s.co_entidade IS NULL
        """)
        self.assertEqual(faltantes_escola, 0, f"{faltantes_escola} escolas na fato ausentes em gold.dim_escola!")

        faltantes_mun = self.auditor.query_count("""
            SELECT COUNT(DISTINCT s.co_municipio)
            FROM gold.dim_escola s
            LEFT JOIN gold.dim_municipio m ON s.co_municipio = m.co_municipio
            WHERE m.co_municipio IS NULL
        """)
        self.assertEqual(faltantes_mun, 0, f"{faltantes_mun} municípios ausentes em gold.dim_municipio!")

    def test_05_distribuicao(self):
        ufs_distintas = self.auditor.query_count("SELECT COUNT(DISTINCT sg_uf) FROM gold.dim_municipio")
        self.assertGreaterEqual(ufs_distintas, 1, "Nenhuma UF encontrada em gold.dim_municipio!")

    def test_06_casos_sujos(self):
        invalidas = self.auditor.query_count("""
            SELECT COUNT(*) FROM gold.dim_escola
            WHERE tp_dependencia NOT IN (1, 2, 3, 4)
        """)
        self.assertEqual(invalidas, 0, f"Detectados {invalidas} registros com tp_dependencia fora do domínio!")


if __name__ == "__main__":
    unittest.main()
