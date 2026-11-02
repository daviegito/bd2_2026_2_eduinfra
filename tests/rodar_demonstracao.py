import sys
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from eduinfra.configuracao import carregar_configuracao

def injetar_defeito(configuracao):
    print("\n--- [1/3] Injetando defeito de propósito na Gold ---")
    caminho_tabela = configuracao.diretorio_dados / "warehouse" / "vistoria_item"

    if not caminho_tabela.exists():
        print(f"Erro: Tabela não encontrada em {caminho_tabela}. Rode a transformação primeiro!")
        sys.exit(1)

    print("Lendo amostra da tabela para garantir compatibilidade...")
    dt = DeltaTable(str(caminho_tabela))

    sample_table = dt.to_pyarrow_table()

    if sample_table.num_rows == 0:
        print("Erro: A tabela está vazia. Não é possível injetar defeito baseado em dados existentes.")
        sys.exit(1)

    # Pegamos a primeira linha para clonar a estrutura e os tipos de dados
    first_row = sample_table.slice(0, 1)

    # Exportamos TODAS as colunas dessa linha para um dicionário Python nativo
    data_dict = first_row.to_pydict()

    # Injetamos o defeito modificando apenas a coluna alvo
    data_dict["situacao"] = ["ERRO_DE_DOMINIO"]

    # Recriamos a tabela de defeito com todas as colunas intactas
    defeito_table = pa.Table.from_pydict(data_dict, schema=sample_table.schema)

    print(f"Injetando registro sujo...")

    write_deltalake(str(caminho_tabela), defeito_table, mode="append")
    print("✅ Defeito injetado com sucesso!")

def rodar_testes():
    print("\n--- [2/3] Executando testes de auditoria (E3) ---")
    import unittest

    # Garante o path pro import dentro do container
    if "/app" not in sys.path:
        sys.path.insert(0, "/app")

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_e3_gold")
    runner = unittest.TextTestRunner(verbosity=2)
    resultado = runner.run(suite)

    if not resultado.wasSuccessful():
        print("\n" + "="*50)
        print("❌ RESULTADO: O teste FALHOU como esperado!")
        print("A auditoria detectou o erro de domínio (situacao='ERRO_DE_DOMINIO').")
        print("Isso prova que o sistema de testes é eficaz contra dados sujos.")
        print("="*50)
    else:
        print("\n" + "="*50)
        print("⚠️ AVISO: O teste PASSOU.")
        print("Isso significa que a auditoria NÃO detectou o defeito.")
        print("Verifique se o teste de 'casos_sujos' no arquivo test_e3_gold.py está rigoroso o suficiente.")
        print("="*50)


def main():
    print("=== INICIANDO DEMONSTRAÇÃO DE AUDITORIA E3 ===")
    config = carregar_configuracao()

    try:
        injetar_defeito(config)
        rodar_testes()
    except Exception as e:
        print(f"Erro durante a demonstração: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
