# 0007 — Registrar telemetria por transação no consumidor do CDC e isolar linhas ruins numa fila de erros

- **Status:** aceito, aprovado por Gabriel Lopes de Amorim no PR #59, depois da revisão em que Davi Coelho achou o teste que dependia do intervalo entre dois commits
- **Data:** 2026-10-31
- **Decisores:** Artur Mendonça Arruda

## Contexto

O [ADR 0004](0004-ingestao.md) adotou um consumidor próprio de `pgoutput` e registrou que latência ponta a ponta e p95 não foram medidos. Artur definiu oito métricas para o fluxo: checksum de linhas, contagem por operação, fora de ordem, latência ponta a ponta, atraso de captura, atraso de carga, desvio de esquema e volume da fila de erros. O consumidor não produzia a maioria delas.

**Carga.** Cerca de 3,2 milhões de itens de vistoria, 17 por vistoria, com estimativa de 500 vistorias por dia (8.500 itens), ainda não medida em produção. A [caracterização](../caracterizacao.md) não declara latência tolerada para a propagação até o Delta, e os 300 ms da origem não são SLA de ingestão.

**Padrões de acesso.** A origem recebe só inserções, uma transação por vistoria. O consumidor lê o slot a cada 2 s e grava por merge na chave. A conferência é rara e varre as duas tabelas.

**Restrições.** Execução local pelo Compose, sem serviço pago. Reaproveitar Python, PostgreSQL e Delta, que a squad já opera. Nada de dado pessoal fora da origem, porque `vistoria.observacao` é texto livre.

**Falhas do consumidor anterior,** provocadas em banco descartável:

| Falha | Comportamento |
| --- | --- |
| `ocorrido_em = '-infinity'`, aceito pela origem | `DataError` em toda passada. Nada depois dessa linha chega ao Delta |
| `RENAME COLUMN` | `KeyError` em toda passada, mesmo depois de desfazer a migração |
| `ADD COLUMN` com valor | Valor perdido, sem aviso |
| `UPDATE` por fora dos gatilhos | `ValueError` em toda passada |

## Alternativas consideradas

### A. Opção nula — manter o consumidor e medir só por fora

Cronometrar de fora até a vistoria aparecer no Delta. Não muda o consumidor, e nenhuma dessas falhas apareceu nos dados reais até agora. Não foi escolhida porque não separa captura de carga, não mede a operação normal e deixa sem objeto a fila e o desvio de esquema. Um `CHECK` na origem resolveria só as datas.

### B. Telemetria e fila de erros próprias, em Delta — escolhida

O consumidor grava uma linha por transação em `telemetria_cdc`, desvia o que não converte para `fila_de_erros_cdc` e o slot avança. Cobra duas tabelas novas e um item pode ficar fora do Delta até alguém agir.

### C. Telemetria própria, parar e alertar diante de linha ruim

Nada avança sem ser visto, e não exige fila. Foi eliminada porque uma linha seguraria todas as outras: o limite de 1 GB de WAL levaria 200,8 dias com a carga estimada, mas o frescor morreria na hora.

### D. Debezium com Kafka Connect e sua fila de erros

Oferece fila e métricas prontas, com ao menos dois serviços a mais. **Não foi prototipada nem medida**. Foi eliminada pela restrição de stack, sem vantagem quantitativa declarada.

## Medição

Censo 2024 mais vistorias de teste, em duas rodadas de 30 amostras por cenário, no Docker Desktop com outros projetos na mesma máquina. Medianas se repetem; p95 são indicativos.

| Medida, em segundos, por dentro | Rodada 1 | Rodada 2 |
| --- | ---: | ---: |
| Ponta a ponta, evento recente: mediana / p95 | 1,90 / 9,06 | 1,62 / 4,94 |
| Ponta a ponta, evento atrasado: mediana / p95 | 4,10 / 9,68 | 4,10 / 10,08 |
| Merge, evento recente / atrasado: mediana | 0,71 / 2,77 | 0,29 / 2,50 |

Por fora, as medianas ficaram a até 0,21 s das de dentro. O checksum achou 3.174.801 linhas iguais e 0 diferentes. Sob 4 escritores concorrentes, uma recarga por marca d'água a cada 2 s, simulada sobre os commits medidos, perderia 7 e 6 de 120 transações sem margem, e o slot entregou todas.

| Alternativa | Separa captura de carga | Linha ruim para o fluxo | Conta fila e desvio | Serviços novos |
| --- | :---: | :---: | :---: | ---: |
| A — opção nula | não | sim | não | 0 |
| B — telemetria e fila | sim | não | sim | 0 |
| C — telemetria, parar e alertar | sim | sim | só desvio | 0 |
| D — Debezium | não medido | não medido | não medido | ao menos 2 |

Como reproduzir, em banco de desenvolvimento:

```bash
docker compose run --rm carga medir-cdc --repeticoes 30
docker compose run --rm carga auditar-cdc
uv run python docs/evidencias/metricas-cdc/falhas_depois_da_mudanca.py
uv run pytest tests/test_resiliencia_cdc_integracao.py -q
```

## Decisão

Escolhemos B, com estas regras:

- **Fila de erros.** Item que não converte e alteração fora do insert-only vão para a fila, só com a chave e o motivo, e o slot avança. Falha de infraestrutura continua interrompendo a passada, porque tentar de novo resolve.
- **Telemetria.** Uma linha por transação, com o relógio do PostgreSQL.
- **Publicação.** A migração 0007 publica as quatro operações em todos os bancos. Só com `INSERT`, um `UPDATE` indevido nem seria contado.
- **Contrato de esquema.** O consumidor confere o WAL contra `contrato_cdc.py`, e a auditoria confere o catálogo. Desvio aditivo gera alerta e as linhas seguem.
- **Checksum.** Tabela inteira, sob demanda, em vez de amostra: custou 17,4 s e 469 MiB em 3.167.729 linhas (duas repetições levaram 15,8 s e 41,8 s), e só 0,07% das linhas tinham vindo do CDC, caminho que uma amostra quase não testaria.
- **Frescor.** Tempo desde o início da última passada concluída, com SLA proposto de 60 s. `auditar-cdc` sai com código 1 em qualquer violação.

## Consequências

**O que ganhamos:** uma linha ruim não para mais o fluxo, a latência é decomposta sem depender de vistoria de teste, e a vantagem do CDC sobre a marca d'água tem número.

**O que perdemos:**

- Um item pode ficar fora do Delta até alguém olhar a fila. Quem opera precisa rodar `auditar-cdc`, porque além dela só o log avisa.
- Não há reprocessamento automático. `publicar-analitico --forcar` relê a origem inteira, aceita `-infinity` e cria uma partição com esse nome.
- `contrato_cdc.py` precisa mudar junto com toda migração de `vistoria` ou `vistoria_item`. Se o autor esquecer, a auditoria acusa desvio.
- Um desvio do insert-only fica como violação para sempre, porque a auditoria não o marca como tratado.

**Riscos assumidos:** a validação da origem continua aberta. Os p95 vieram de uma máquina não dedicada. O merge de 50,38 s depois de religar o consumidor não foi investigado.

**O que se torna irreversível:** avançar o slot além de uma linha rejeitada não tem volta, porque o CDC não a entrega de novo. A origem continua com o dado, então recuperar custa uma releitura da origem inteira, sem medição recente. As duas tabelas novas podem ser apagadas sem efeito sobre `eventos_vistoria`.

## Gatilho de revisão

- Rever a fila se ela receber mais de **0,1% dos itens em um mês**: aí o problema é a validação da origem.
- Rever a telemetria e o laço se o p95 ponta a ponta do evento recente passar de **60 s** em operação normal.
- Adicionar compactação se a mediana do merge na partição do mês passar de **5 s**.
- Trocar o checksum por impressão por partição se a auditoria passar de **5 minutos** ou de **1 GB** de memória.

## Evidências

- [Métricas do CDC, saídas brutas e scripts](../metricas-cdc.md#evidencias).
- [Migração 0007](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/d146d76/alembic/versions/0007_publica_todas_as_operacoes.py) e o commit `703af55`, que tinha a outra versão da 0005.
- [Testes de resiliência](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/eaefafe/tests/test_resiliencia_cdc_integracao.py), em banco descartável.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Telemetria, fila de erros, publicação e checksum do CDC | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 31 de outubro de 2026 |
| 1.1 | Inclui padrões de acesso, riscos assumidos e evidências brutas; reduz o texto | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 31 de outubro de 2026 |
| 1.2 | Passa de proposto a aceito, com a aprovação no PR #59 | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 9 de novembro de 2026 |
