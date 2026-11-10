import sys
import duckdb
from eduinfra.configuracao import carregar_configuracao

def injetar_defeito(conexao, dsn):
    print("\n--- [1/3] Injetando defeito de propósito na Gold ---")

    # Verifica se a tabela Gold existe no PostgreSQL
    tabelas = [row[0] for row in conexao.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'gold'"
    ).fetchall()]

    if "fato_vistoria_item" not in tabelas:
        print("Erro: Tabela gold.fato_vistoria_item não encontrada no banco. Rode a transformação primeiro!")
        sys.exit(1)

    print("Injetando registro sujo na camada Gold do PostgreSQL...")

    # Busca um co_entidade real para não quebrar a integridade referencial
    co_entidade = conexao.execute("SELECT co_entidade FROM pg.gold.dim_escola LIMIT 1").fetchone()
    if not co_entidade:
         print("Erro: dim_escola está vazia.")
         sys.exit(1)

    co_entidade = co_entidade[0]

    # Injeta a linha com a situação proibida
    conexao.execute(f"""
        INSERT INTO pg.gold.fato_vistoria_item (vistoria_id, co_entidade, item_codigo, situacao)
        VALUES (999999999, {co_entidade}, 'ITEM_DEMO', 'ERRO_DE_DOMINIO')
    """)

    print("✅ Defeito injetado com sucesso!")

def rodar_testes():
    print("\n--- [2/3] Executando testes de auditoria (E3) ---")
    import unittest

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

    conexao = duckdb.connect()
    conexao.execute("INSTALL postgres; LOAD postgres;")
    conexao.execute(f"ATTACH '{config.dsn}' AS pg (TYPE POSTGRES);")

    try:
        injetar_defeito(conexao, config.dsn)
        rodar_testes()
    except Exception as e:
        print(f"Erro durante a demonstração: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Limpando defeito injetado...")
        try:
             conexao.execute("DELETE FROM pg.gold.fato_vistoria_item WHERE situacao = 'ERRO_DE_DOMINIO'")
        except Exception:
             pass

if __name__ == "__main__":
    main()
