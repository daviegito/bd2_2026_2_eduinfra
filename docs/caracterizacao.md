# Caracterização da Carga de Trabalho — Método de Decisão (Passo 1)

## 1. Escopo e Domínio
* **Domínio:** Vistorias de infraestrutura escolar.
* **Pergunta de Gestão:** A infraestrutura de uma escola se reflete no desempenho dos seus alunos no ENEM?

---

## 2. Volumetria e Dimensionamento
Os volumes de dados foram medidos com base na carga do **Censo Escolar 2024** e estruturados nos cenários de produção e testes:

| Cenário / Entidade | Tabela `escola` | Tabela `vistoria_item` (Média: 17 itens/escola) | Objetivo do Cenário |
| :--- | :--- | :--- | :--- |
| **Produção Atual (Censo 2024)** | 181.065 linhas | 3.078.105 linhas (por carga anual) | Operação e base real do sistema |
| **Benchmark Local (Stress)** | 100.000 linhas | ~1.700.000 linhas | Testar estresse de consultas analíticas |
| **Demonstração (MVP/CI)** | 1.000 linhas | ~17.000 linhas | Testes funcionais rápidos e integração contínua |

---

## 3. Perfil de Tráfego (Taxas de Acesso)

### Taxa de Escrita
* **Pico Operacional:** ~500 vistorias por dia.
* **Taxa Média:** ~0,006 vistorias por segundo.

### Taxa de Leitura
* **Frequência:** De 10 a 100 requisições diárias por gestor.

---

## 4. Padrões de Acesso e Latência

### Padrões de Acesso Comuns
* **Leituras OLTP:** Consultas diretas por chave (`co_entidade`) e filtros geográficos/administrativos (`municipio`, `UF`, `dependencia_administrativa`).
* **Agregações Analíticas (OLAP):** Varreduras por município, ano e itens para geração de médias e gráficos.
* **Operações de Escrita:** Inserts contínuos de novas vistorias. Sem *updates* em fatos históricos.

### Requisitos de Latência (SLA)
* **Estado Corrente (Leitura por escola):** < 300 ms (Medido na view `escola_estado_atual`: 0,13 ms a 0,24 ms).
* **Consultas Agregadas (Relatórios):** < 10 segundos para dashboards interativos em amostras médias.

---

## 5. Premissas de Arquitetura
* **Vantagem:** O histórico completo é preservado, permitindo análises temporais sobre a evolução da infraestrutura de cada escola.
* **Desafio / Custo:** O volume de linhas da tabela `vistoria_item` cresce de forma linear a cada nova carga.
* **Mitigação:** Exige planejamento prévio de índices estratégicos e particionamento de dados caso o volume ultrapasse a capacidade de hardware de um único nó (**limite estabelecido de 50 milhões de linhas**).

---

## 6. Diretrizes e Recomendações de Testes
Para validar a performance e o comportamento do banco de dados, utilize os comandos e estratégias abaixo:

### Comandos Úteis
* **Gerar Amostras Locais:**
  ```bash
  eduinfra gerar_amostra --escolas [NÚMERO_DE_ESCOLAS]
  ```

### Estratégia de Testes e CI/CD
* **Testes Funcionais (CI):** Configure o pipeline de Integração Contínua para rodar testes rápidos usando o comando `--escolas 1000`.
* **Benchmarks de Consulta:** Execute um job separado gerando uma amostra de 100 mil escolas. Meça o tempo de resposta da consulta `escola_estado_atual` por `co_entidade` executada em lote .



## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Documentação da caracterização da carga de trabalho e perfil de tráfego do banco | [Lucas Mendonça Arruda](https://github.com/lucasarruda9) |  | 28 de setembro de 2026 |
