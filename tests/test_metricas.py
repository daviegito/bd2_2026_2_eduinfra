import re
import unittest
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
PASTA = RAIZ / "metricas"
MODELOS_DA_GOLD = RAIZ / "transformacao" / "models" / "marts"

OBRIGATORIOS = (
    "nome", "titulo", "versao", "status", "historico", "dono", "descricao", "grao", "fonte",
    "formula", "filtros", "supressao", "consumidores",
)
STATUS = {"ativa", "rascunho", "descontinuada"}
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def carregar() -> dict[Path, dict]:
    return {arquivo: yaml.safe_load(arquivo.read_text(encoding="utf-8")) for arquivo in sorted(PASTA.glob("*.yml"))}


def vazio(valor) -> bool:
    return valor is None or (isinstance(valor, (str, list, dict)) and not valor)


class TestDefinicoesDeMetricas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metricas = carregar()

    def test_ha_metricas_definidas(self):
        self.assertGreaterEqual(len(self.metricas), 4, "a E4 pede de 4 a 6 métricas definidas")

    def test_campos_obrigatorios_preenchidos(self):
        for arquivo, metrica in self.metricas.items():
            with self.subTest(arquivo.name):
                self.assertIsInstance(metrica, dict)
                faltando = [campo for campo in OBRIGATORIOS if campo not in metrica]
                self.assertEqual(faltando, [], f"{arquivo.name}: campos ausentes")
                vazios = [campo for campo in OBRIGATORIOS if vazio(metrica[campo])]
                self.assertEqual(vazios, [], f"{arquivo.name}: campos vazios")
                for campo in ("fonte", "filtros", "consumidores"):
                    self.assertIsInstance(metrica[campo], list, f"{arquivo.name}: {campo} deve ser uma lista")

    def test_nome_bate_com_o_arquivo(self):
        for arquivo, metrica in self.metricas.items():
            with self.subTest(arquivo.name):
                self.assertEqual(metrica["nome"], arquivo.stem)

    def test_versao_e_status_validos(self):
        for arquivo, metrica in self.metricas.items():
            with self.subTest(arquivo.name):
                self.assertRegex(str(metrica["versao"]), SEMVER)
                self.assertIn(metrica["status"], STATUS)
                # Abaixo de 1.0.0 a definição ainda está em discussão.
                if metrica["status"] == "ativa":
                    self.assertFalse(str(metrica["versao"]).startswith("0."), "métrica ativa precisa de versão >= 1.0.0")

    def test_versao_registrada_no_historico(self):
        for arquivo, metrica in self.metricas.items():
            with self.subTest(arquivo.name):
                historico = metrica["historico"]
                self.assertIsInstance(historico, list)
                for entrada in historico:
                    self.assertTrue({"versao", "data", "mudanca"} <= entrada.keys())
                # A versão atual precisa ser a primeira entrada do histórico.
                self.assertEqual(str(historico[0]["versao"]), str(metrica["versao"]), "versão nova sem entrada no histórico")

    def test_supressao_declarada(self):
        for arquivo, metrica in self.metricas.items():
            with self.subTest(arquivo.name):
                supressao = metrica["supressao"]
                self.assertTrue({"regra", "minimo"} <= supressao.keys())
                self.assertIsInstance(supressao["minimo"], int)

    def test_fontes_da_gold_existem(self):
        for arquivo, metrica in self.metricas.items():
            for fonte in metrica["fonte"]:
                schema, _, tabela = fonte.partition(".")
                if schema == "gold":
                    with self.subTest(arquivo.name, fonte=fonte):
                        self.assertTrue((MODELOS_DA_GOLD / f"{tabela}.sql").is_file(), f"modelo dbt de {fonte} não existe")

    def test_painel_usa_o_minimo_da_definicao(self):
        texto = (RAIZ / "src" / "eduinfra" / "painel.py").read_text(encoding="utf-8")
        minimo_no_painel = int(re.search(r"^MINIMO_ESCOLAS = (\d+)$", texto, re.M).group(1))
        definicao = next(m for m in self.metricas.values() if m["nome"] == "media_enem_escolas")
        self.assertEqual(minimo_no_painel, definicao["supressao"]["minimo"])


if __name__ == "__main__":
    unittest.main()
