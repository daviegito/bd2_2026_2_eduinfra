import sys
import subprocess
from pathlib import Path
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from eduinfra.configuracao import Configuracao

def injetar_defeito(configuracao: Configuracao):
    print("\n--- [1/3] Injetando defeito de propósito na Gold ---")
    caminho_tabela = configuracao.diretorio_dados / "warehouse" / "vistoria_item"

    if not caminho_tabela.exists():
        print(f"Erro: Tabela não encontrada em {caminho_tabela}. Rode a transformação primeiro!")
        sys.exit(1)

    print("Lendo amostra da tabela para garantir compatibilidade...")
    dt = DeltaTable(str(caminho_tabela))

    if dt.num_rows == 0:
        print("Erro: A tabela está vazia. Não é possível injetar defeito baseado em dados existentes.")
        sys.exit(1)

    # Pegamos a primeira linha para clonar a estrutura e os tipos de dados
    # Usamos pyarrow para evitar a dependência de pandas
    sample_table = dt.to_arrow()
    first_row = sample_table.slice(0, 1)

    # Criamos o novo registro com o defeito usando PyArrow
    # Construímos um dicionário com os dados da primeira linha, mudando apenas a 'situacao'
    data_dict = {
        "ano_censo": [first_row.column("ano_censo")[0]],
        "sg_uf": [first_row.column("sg_uf")[0]],
        "co_entidade": [first_row.column("co_entidade")[0]],
        "item_codigo": [first_row.column("item_codigo")[0]],
        "situacao": ["ERRO_DE_DOMINIO"]  # <--- O DEFEITO (valor proibido)
    }

    defeito_table = pa.Table.from_pydict(data_dict)

    print(f"Injetando registro sujo: {data_dict}")

    # Escreve o defeito na tabela usando modo 'append'
    write_deltalake(str(caminho_tabela), defeito_table, mode="append")
    print("✅ Defeito injetado com sucesso!")

def rodar_testes():
    print("\n--- [2/3] Executando testes de auditoria (E3) ---")
    # Executa o unittest apontando para o arquivo de testes
    # Usamos -m unittest para garantir que o python path esteja correto
    resultado = subprocess.run([sys.executable, "-m", "unittest", "tests/test_e3_gold.py"])

    if resultado.returncode != 0:
        print("\n" + "="*50)
        print("❌ RESULTADO: O teste FALHOU como esperado!")
        print("A auditoria detectou o erro de domínio (situacao='ERRO_DE_DOMINIO').")
        print("Isso prova que o sistema de testes é eficaz contra dados sujos.")
        print("="*50)
    else:
        print("\n" + "="*50)
        print("⚠️ AVISO: O teste PASSOU.")
        print("Isso significa que a auditoria NÃO detectou o defeito.")
        print("Verifique se o teste de 'casos_sujos' no arquivo test_e3_gold.py está correto.")
        print("="*50)
from eduinfra.configuracao import carregar_configuracao

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
