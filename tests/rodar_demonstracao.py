import sys
import pyarrow as pa
from eduinfra.configuracao import carregar_configuracao
import psycopg

def injetar_defeito(configuracao):
    print("\n--- [1/3] Injetando defeito de propósito na Gold (PostgreSQL) ---")
    with psycopg.connect(configuracao.dsn, autocommit=True) as conn:
        conn.execute("""
            INSERT INTO gold.fato_vistoria_item (co_entidade, item_codigo, situacao, quantidade)
            SELECT co_entidade, item_codigo, 'ERRO_DE_DOMINIO', 1
            FROM gold.fato_vistoria_item
            LIMIT 1
        """)
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
    finally:
        with psycopg.connect(config.dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM gold.fato_vistoria_item WHERE situacao = 'ERRO_DE_DOMINIO'")

if __name__ == "__main__":
    main()
