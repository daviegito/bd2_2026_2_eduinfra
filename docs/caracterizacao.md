# Caracterização da Carga de Trabalho — Método de Decisão (Passo 1)

Os números medidos desta página vêm do banco populado, não são estimados. Para
reproduzi-los, rode `uv run eduinfra caracterizar` depois da carga. Onde há
estimativa, ela está identificada como tal.

## 1. Escopo e domínio

* **Domínio:** Vistorias de infraestrutura escolar.
* **Pergunta de gestão:** A infraestrutura de uma escola se reflete no desempenho dos seus alunos no ENEM?

## Ambiente da medição

| Item | Valor |
| --- | --- |
| SGBD | PostgreSQL 16 (imagem `pgvector/pgvector:pg16`) |
| Execução | Docker Desktop em Windows 11, disco NVMe local |
| Fonte | Microdados do Censo Escolar 2024 (INEP), arquivo da educação básica |
| Recorte | Escolas com `TP_SITUACAO_FUNCIONAMENTO = 1`, ano completo |
| Data do evento | 29/05/2024, data de referência do Censo |

## 2. Volume

| Métrica | Valor medido |
| --- | --- |
| Municípios | 5.570 |
| Escolas em atividade | 181.065 |
| Vistorias registradas | 181.065 |
| Itens de vistoria | 3.078.105 |
| Itens com situação `existente` | 1.699.347 |
| Itens com situação `nao_informado` | 0 |

O arquivo bruto do Censo tem 215.545 escolas. A diferença para as 181.065
carregadas são as escolas que não estavam em atividade em 2024, descartadas na
transformação.

| Tabela | Tamanho em disco |
| --- | --- |
| `vistoria_item` | 369 MB |
| `vistoria` | 45 MB |
| `escola` | 23 MB |
| `municipio` | 520 kB |
| `tecnico` | 48 kB |
| `item_infraestrutura` | 48 kB |

### Cenários de teste (projetado, não medido)

Para estresse de consultas analíticas e para testes funcionais rápidos, sem
depender da carga completa do Censo, use o gerador de amostra sintética
(seção 8). Os volumes abaixo são alvos de dimensionamento, não medições:

| Cenário | Tabela `escola` | Tabela `vistoria_item` (média de 17 itens/escola) | Objetivo |
| --- | --- | --- | --- |
| Benchmark local (stress) | 100.000 linhas | ~1.700.000 linhas | testar estresse de consultas analíticas |
| Demonstração (MVP/CI) | 1.000 linhas | ~17.000 linhas | testes funcionais rápidos e integração contínua |

## 3. Taxa de escrita

| Cenário | Valor | Origem do número |
| --- | --- | --- |
| Carga anual de linha de base | 181.065 vistorias e 3.078.105 itens por execução | Medido |
| Tempo da carga a partir do CSV já baixado | 2 min 51 s | Medido |
| Tempo de `docker compose up` em ambiente vazio, incluindo download e migrações | 4 min 10 s | Medido |
| Reexecução da carga já aplicada | 0 linhas inseridas, 1 min 48 s | Medido |
| Operação diária da secretaria | cerca de 500 vistorias por dia (~0,006/s) | Estimativa da equipe, declarada na aba 1 da planilha de acompanhamento |

A carga anual concentra quase toda a escrita do sistema. O uso diário pelo
aplicativo é comparativamente desprezível em volume, mas é o que exige latência.

## 4. Taxa e padrão de leitura

| Consulta | Frequência esperada | Latência medida | Latência tolerada |
| --- | --- | --- | --- |
| Estado atual de uma escola (`escola_estado_atual` por `co_entidade`) | Alta, a cada tela do aplicativo | 0,13 a 0,24 ms | < 300 ms |
| Série histórica de um item de uma escola | Média | 0,04 ms | < 300 ms |
| Consulta de um gestor (painel) | 10 a 100 requisições diárias por gestor, estimativa da equipe | não medida nesta entrega | < 10 s para dashboards interativos |
| Agregação nacional por rede de escolas sem biblioteca | Baixa, painel e pipeline | 1.890 ms | < 5 s enquanto for consulta de painel |
| Extração completa para a camada analítica | Lote, anual | não medida nesta entrega | minutos |

As duas primeiras usam o índice `vistoria_por_escola_no_tempo` e ficam três
ordens de grandeza abaixo do tolerado. A agregação nacional varre
`vistoria_item` inteira, ordena em disco e é o caso que justifica o gatilho de
revisão registrado no
[ADR 0001](adr/0001-modelagem-insert-only-do-sistema-de-origem.md): se essa
classe de consulta migrar para o caminho interativo do assistente, a view passa
a materializada.

| Escrita | Latência medida | Latência tolerada |
| --- | --- | --- |
| Registrar uma vistoria | 15,5 ms | < 300 ms na tela do técnico |

## 5. Padrão de acesso

- **Escrita:** pontual, uma vistoria por vez, disparada pelo aplicativo da
  secretaria. Insert-only, sem contenção de linha, sem transação longa.
- **Leitura transacional (OLTP):** por chave, sempre filtrando `co_entidade`,
  com filtros geográficos e administrativos complementares (`municipio`, `UF`,
  `dependencia_administrativa`).
- **Leitura analítica (OLAP):** varreduras completas por município, ano e item,
  em lote, anual, consumida pela E2.
- **Concorrência esperada:** dezenas de técnicos simultâneos em campo.
  Estimativa da equipe, obtida do número de escolas por regional; não foi
  medida sob carga.

## 6. Disponibilidade e garantias

| Dimensão | Decisão |
| --- | --- |
| ACID exigido | Atomicidade, isolamento e durabilidade. Consistência referencial é imposta por chave estrangeira. |
| Do que se abre mão | Nada na origem. O relaxamento acontece na camada analítica, não aqui. |
| Disponibilidade | Alta no horário comercial. Vistoria é trabalho de campo diurno. |
| Janela de indisponibilidade tolerada | A carga anual pode rodar com o aplicativo fora do ar. |

## 7. Premissas de arquitetura e risco de crescimento

* **Vantagem:** o histórico completo é preservado, permitindo análises
  temporais sobre a evolução da infraestrutura de cada escola.
* **Desafio/custo:** o volume de linhas da tabela `vistoria_item` cresce de
  forma linear a cada nova carga.
* **Mitigação:** exige planejamento prévio de índices estratégicos e
  particionamento de dados caso o volume ultrapasse a capacidade de hardware
  de um único nó (limite estabelecido de 50 milhões de linhas).

## 8. Diretrizes e recomendações de testes

Para validar a performance e o comportamento do banco sem depender da carga
completa do Censo, use o gerador de amostra sintética:

```bash
eduinfra gerar_amostra --escolas [NÚMERO_DE_ESCOLAS]
```

* **Testes funcionais (CI):** rode o pipeline de integração contínua com
  `--escolas 1000`.
* **Benchmarks de consulta:** gere uma amostra de 100 mil escolas num job
  separado e meça o tempo de resposta da consulta `escola_estado_atual` por
  `co_entidade` executada em lote.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Documentação da caracterização da carga de trabalho e perfil de tráfego do banco | [Lucas Mendonça Arruda](https://github.com/lucasarruda9) |  | 28 de setembro de 2026 |
| 1.1 | Consolida números medidos na base populada (volume, taxa de escrita e leitura, disponibilidade e garantias), separando o que foi medido do que é estimativa | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 28 de setembro de 2026 |
