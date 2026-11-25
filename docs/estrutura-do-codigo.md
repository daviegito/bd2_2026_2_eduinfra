# Estrutura do código

Até a E3, `src/eduinfra/` tinha 37 módulos soltos na mesma pasta, com ingestão, CDC, Gold, medições e
demonstrações misturados. No feedback de 16 de novembro de 2026 a docência apontou a bagunça, e a
[issue #96](https://github.com/daviegito/bd2_2026_2_eduinfra/issues/96) agrupou os módulos em pacotes pelo caminho do dado. Nenhum comando mudou de
nome e o `eduinfra --help` saiu idêntico ao de antes.

## Onde fica cada camada

| Camada | Onde está | O que guarda |
| --- | --- | --- |
| Origem (OLTP) | `alembic/versions/` e [`origem/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/src/eduinfra/origem/) | esquema, carga da origem a partir da Silver, vistorias sintéticas e amostra |
| Ingestão em lote | [`ingestao/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/src/eduinfra/ingestao/) | download do INEP (com a cadeia TLS do ADR 0002) e ingestão idempotente do Censo e do ENEM |
| Bronze e Silver | [`lake/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/src/eduinfra/lake/) | Silver do Censo e do ENEM em Parquet e a tabela Delta da camada analítica |
| CDC | [`cdc/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/src/eduinfra/cdc/) | slot lógico, decodificação do pgoutput, fonte canônica de eventos, janelas, fila de erros, telemetria, auditoria e recuperação |
| Gold | [`transformacao/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/transformacao/) e [`gold/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/src/eduinfra/gold/) | os modelos dbt ficam em `transformacao/models`; o pacote Python prepara as fontes do dbt e mede o SLA de frescor |
| Consumo | [`consumo/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/src/eduinfra/consumo/) | painel HTML gerado a partir das tabelas de consumo da Gold |
| Segurança | [`seguranca/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/src/eduinfra/seguranca/) | papéis, RLS por UF, recorte de coluna e demonstração de privilégios mínimos |
| Experimentos | [`experimentos/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/src/eduinfra/experimentos/) | medições, comparações e demonstrações citadas nos ADRs; nada aqui roda no pipeline agendado |

[`cli.py`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/src/eduinfra/cli.py) e [`orquestrador.py`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/src/eduinfra/orquestrador.py)
ficaram na raiz do pacote porque são pontos de entrada: o `pyproject.toml`, o Dockerfile e o
`docker-compose.yml` continuam chamando `eduinfra.cli` e `eduinfra.orquestrador`. O `nucleo/` guarda a
configuração por variável de ambiente e a gravação da saída dos comandos em arquivo.

O `cli.py` monta o parser e despacha. Cada pacote registra os próprios comandos no seu `comandos.py`, e o
[teste da CLI](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/tests/test_cli.py) falha se um comando aceito pelo parser não tiver quem o execute.

## Camadas da Gold no dbt

O projeto dbt segue o [guia de estrutura do dbt](https://docs.getdbt.com/best-practices/how-we-structure/1-guide-overview):
staging como views, intermediate e marts como tabelas.

| Pasta | Modelos | Para quem |
| --- | --- | --- |
| `models/staging/` | `stg_*` | uma view por fonte, sem regra de negócio |
| `models/intermediate/` | `int_mudanca_item`, `int_desempenho_escola`, `int_desempenho_escola_item` | passos internos; nenhum consumidor lê daqui |
| `models/marts/` | `dim_*` e `fato_*` | a estrela: núcleo da Gold, com histórico (SCD tipo 2) |
| `models/marts/gestao/` | `desempenho_por_recorte`, `desempenho_por_item`, `comparacao_com_sem_item`, `perfil_alto_baixo_desempenho`, `infra_por_uf`, `cobertura_enem`, `escola_desempenho_infra` | tabelas de consumo, já agregadas por UF, rede e item, que o painel lê |

As tabelas de `gestao/` seguem o padrão usual de Gold: a estrela fica como modelo central, e por cima dela
saem tabelas largas e agregadas para quem consome. A regra de supressão (menos de 10 escolas com nota não
publica média) e a faixa de desempenho ficam no `dbt_project.yml` como `minimo_escolas` e
`faixas_de_desempenho`, num lugar só. O painel está declarado como exposição do dbt, então aparece na
linhagem da documentação gerada.

## Dependências entre pacotes

Um pacote só importa os que vêm antes dele no caminho do dado, e nenhum pacote do pipeline importa
`experimentos`. O [teste de estrutura](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/tests/test_estrutura_do_codigo.py) falha se essa regra
quebrar ou se um módulo importar uma função com sublinhado de outro.

| Pacote | Pode importar |
| --- | --- |
| `nucleo` | nenhum |
| `lake` | `nucleo` |
| `origem` | `nucleo`, `lake` |
| `ingestao` | `nucleo`, `lake`, `origem` |
| `cdc` | `nucleo` |
| `gold` | `nucleo`, `origem` |
| `consumo` | `nucleo` |
| `seguranca` | `nucleo` |
| `experimentos` | todos, menos `consumo` |

## O que mudou além do lugar dos arquivos

- Os nomes que se repetiam com o pacote mudaram: `cdc.py` virou `cdc/replicacao.py` e `ingestao.py` virou
  `ingestao/censo.py`. O sufixo `_cdc` dos outros módulos do CDC ficou, porque `auditoria` e `telemetria`
  já são nomes de variável nesses módulos.
- As duas Silver ficaram juntas em `lake/`: `transformacao.py` virou `silver_censo.py`, e o `enem.py`, que
  monta a Silver do ENEM, virou `silver_enem.py`. O nome `enem.py` passou para a ingestão do ENEM.
- `sample_generator.py` passou a se chamar `amostra.py`, porque era o único módulo com nome em inglês.
- O construtor SQL anterior ao dbt (`modelo_analitico.py`) foi para `experimentos/`. Ele é a opção nula do
  ADR 0005 e ainda responde ao comando `construir-gold`, mas o pipeline agendado constrói a Gold pelo dbt.
- A ingestão importava três funções privadas da carga, e a ingestão do ENEM importava uma da ingestão do
  Censo. As quatro ficaram públicas. A função `executar_carga`, que ninguém chamava, foi apagada.
- `docs/` não mudou de lugar: os links das entregas anteriores e do site apontam para esses caminhos, e o
  menu do site já agrupa os documentos por assunto.

## Caminhos antigos

O AI-USAGE.md e o diário de bordo citam os caminhos como eram na época e não foram reescritos. As tabelas
abaixo servem para ler esses registros. Os links fixados em commit antigo continuam funcionando.

| Antes, em `src/eduinfra/` | Agora |
| --- | --- |
| `configuracao.py` | `src/eduinfra/nucleo/configuracao.py` |
| `sample_generator.py` | `src/eduinfra/origem/amostra.py` |
| `vistorias_de_teste.py` | `src/eduinfra/origem/vistorias_de_teste.py` |
| `caracterizacao.py` | `src/eduinfra/experimentos/caracterizacao.py` |
| `ingestao.py` | `src/eduinfra/ingestao/censo.py` |
| `ingestao_enem.py` | `src/eduinfra/ingestao/enem.py` |
| `enem.py` | `src/eduinfra/lake/silver_enem.py` |
| `microdados.py` | `src/eduinfra/ingestao/microdados.py` |
| `tls.py` | `src/eduinfra/ingestao/tls.py` |
| `cobertura_enem.py` | `src/eduinfra/experimentos/cobertura_enem.py` |
| `carga.py` | `src/eduinfra/origem/carga.py` |
| `cdc.py` | `src/eduinfra/cdc/replicacao.py` |
| `pgoutput.py` | `src/eduinfra/cdc/pgoutput.py` |
| `fluxo_cdc.py` | `src/eduinfra/cdc/fluxo_cdc.py` |
| `contrato_cdc.py` | `src/eduinfra/cdc/contrato_cdc.py` |
| `recuperacao_cdc.py` | `src/eduinfra/cdc/recuperacao_cdc.py` |
| `telemetria_cdc.py` | `src/eduinfra/cdc/telemetria_cdc.py` |
| `fila_de_erros.py` | `src/eduinfra/cdc/fila_de_erros.py` |
| `auditoria_cdc.py` | `src/eduinfra/cdc/auditoria_cdc.py` |
| `demonstracao_cdc.py` | `src/eduinfra/experimentos/demonstracao_cdc.py` |
| `eventos.py` | `src/eduinfra/cdc/eventos.py` |
| `janelas.py` | `src/eduinfra/cdc/janelas.py` |
| `demonstracao_atraso.py` | `src/eduinfra/experimentos/demonstracao_atraso.py` |
| `camada_analitica.py` | `src/eduinfra/lake/camada_analitica.py` |
| `transformacao.py` | `src/eduinfra/lake/silver_censo.py` |
| `modelo_analitico.py` | `src/eduinfra/experimentos/modelo_analitico.py` |
| `fontes_transformacao.py` | `src/eduinfra/gold/fontes_dbt.py` |
| `frescor.py` | `src/eduinfra/gold/frescor.py` |
| `otimizacao_consulta.py` | `src/eduinfra/experimentos/otimizacao_consulta.py` |
| `painel.py` | `src/eduinfra/consumo/painel.py` |
| `comparacao_compressao.py` | `src/eduinfra/experimentos/comparacao_compressao.py` |
| `comparacao_ingestao.py` | `src/eduinfra/experimentos/comparacao_ingestao.py` |
| `comparacao_latencia.py` | `src/eduinfra/experimentos/comparacao_latencia.py` |
| `comparacao_modelagem.py` | `src/eduinfra/experimentos/comparacao_modelagem.py` |
| `comparacao_particionamento.py` | `src/eduinfra/experimentos/comparacao_particionamento.py` |

`cli.py` e `orquestrador.py` não mudaram de lugar.

## Testes

`tests/` repete os pacotes de `src/`. Os testes que não pertencem a um pacote (CLI, orquestrador, estrutura
e verificação de caminhos) ficam na raiz de `tests/`. O comando documentado em [testes.md](testes.md) não
mudou.

| Antes, em `tests/` | Agora |
| --- | --- |
| `test_insert_only.py` | `tests/origem/test_insert_only.py` |
| `test_datas_vistoria_integracao.py` | `tests/origem/test_datas_vistoria_integracao.py` |
| `test_ingestao.py` | `tests/ingestao/test_censo.py` |
| `test_ingestao_enem.py` | `tests/ingestao/test_enem.py` |
| `test_enem.py` | `tests/lake/test_silver_enem.py` |
| `test_auditoria_cdc.py` | `tests/cdc/test_auditoria_cdc.py` |
| `test_contrato_cdc.py` | `tests/cdc/test_contrato_cdc.py` |
| `test_eventos.py` | `tests/cdc/test_eventos.py` |
| `test_fluxo_cdc.py` | `tests/cdc/test_fluxo_cdc.py` |
| `test_fluxo_cdc_integracao.py` | `tests/cdc/test_fluxo_cdc_integracao.py` |
| `test_fonte_de_eventos.py` | `tests/cdc/test_fonte_de_eventos.py` |
| `test_fonte_de_eventos_integracao.py` | `tests/cdc/test_fonte_de_eventos_integracao.py` |
| `test_janelas.py` | `tests/cdc/test_janelas.py` |
| `test_janelas_integracao.py` | `tests/cdc/test_janelas_integracao.py` |
| `test_pgoutput.py` | `tests/cdc/test_pgoutput.py` |
| `test_recuperacao_cdc.py` | `tests/cdc/test_recuperacao_cdc.py` |
| `test_recuperacao_cdc_integracao.py` | `tests/cdc/test_recuperacao_cdc_integracao.py` |
| `test_resiliencia_cdc_integracao.py` | `tests/cdc/test_resiliencia_cdc_integracao.py` |
| `test_slot_ocioso_integracao.py` | `tests/cdc/test_slot_ocioso_integracao.py` |
| `test_telemetria_cdc.py` | `tests/cdc/test_telemetria_cdc.py` |
| `test_camada_analitica.py` | `tests/lake/test_camada_analitica.py` |
| `test_transformacao.py` | `tests/lake/test_silver_censo.py` |
| `test_transformacao_property.py` | `tests/lake/test_silver_censo_property.py` |
| `test_transformacao_unit.py` | `tests/lake/test_silver_censo_unit.py` |
| `fuzz_example_atheris.py` | `tests/lake/fuzz_example_atheris.py` |
| `test_e3_gold.py` | `tests/gold/test_e3_gold.py` |
| `test_fontes_transformacao.py` | `tests/gold/test_fontes_dbt.py` |
| `test_frescor.py` | `tests/gold/test_frescor.py` |
| `test_frescor_integracao.py` | `tests/gold/test_frescor_integracao.py` |
| `test_modelo_analitico.py` | `tests/experimentos/test_modelo_analitico.py` |
| `test_otimizacao_consulta.py` | `tests/experimentos/test_otimizacao_consulta.py` |
| `test_otimizacao_consulta_integracao.py` | `tests/experimentos/test_otimizacao_consulta_integracao.py` |
| `test_transformacao_dbt.py` | `tests/gold/test_transformacao_dbt.py` |
| `rodar_demonstracao.py` | `tests/gold/rodar_demonstracao.py` |
| `test_painel.py` | `tests/consumo/test_painel.py` |
| `test_comparacao_compressao.py` | `tests/experimentos/test_comparacao_compressao.py` |
| `test_comparacao_latencia.py` | `tests/experimentos/test_comparacao_latencia.py` |
| `test_comparacao_latencia_integracao.py` | `tests/experimentos/test_comparacao_latencia_integracao.py` |

## Verificação de caminhos

O [`scripts/verificar_caminhos.py`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/scripts/verificar_caminhos.py) lê todos os `.md` do
repositório e falha se algum citar um caminho de `src/`, `tests/` ou `scripts/` que não existe, ou um link
do GitHub para `main` que não existe. Ficam de fora o AI-USAGE.md e o diário, pelo motivo acima. O script
roda no workflow de documentação, antes do `mkdocs build --strict`, e num workflow próprio em todo pull
request, para pegar o código movido sem o documento acompanhar.

```bash
python scripts/verificar_caminhos.py
```

## Histórico de versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Mapa de pacotes de `src/` e `tests/`, regra de dependência e verificação de caminhos (issues #96 e #97) | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 23 de novembro de 2026 |
| 1.1 | Camadas da Gold no dbt e tabelas de consumo da gestão (issues #94 e #95) | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 23 de novembro de 2026 |
