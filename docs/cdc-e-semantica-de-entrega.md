# Captura de mudanças e semântica de entrega

Este documento cobre as issues #25 (CDC) e #26 (semântica de entrega) da E2. O
[ADR 0004](adr/0004-ingestao.md) consolida a decisão. Esta página reúne o
mecanismo, as garantias, a saída real da demonstração e as medições.

## Mecanismo

A origem publica `vistoria` e `vistoria_item` na publicação `eduinfra_cdc`
(migração 0005), só com `INSERT`. Um consumidor próprio, em Python, lê o slot
lógico `eduinfra_cdc` com o plugin `pgoutput`:

1. `pg_logical_slot_peek_binary_changes` devolve as transações pendentes sem
   consumi-las.
2. [pgoutput.py](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/src/eduinfra/pgoutput.py) decodifica as mensagens Begin,
   Relation, Insert e Commit, conforme o
   [formato do protocolo](https://www.postgresql.org/docs/16/protocol-logicalrep-message-formats.html).
3. [fluxo_cdc.py](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/src/eduinfra/fluxo_cdc.py) enriquece cada item com o
   cabeçalho da vistoria (escola, UF, `ocorrido_em`, `registrado_em`) e grava na
   tabela Delta `eventos_vistoria` por `merge`, inserindo só chaves novas.
4. Só depois da gravação o slot avança, com `pg_replication_slot_advance` até o
   LSN de fim da transação.

Debezium não foi prototipado. O consumidor próprio cobre `INSERT` de duas
tabelas com chave primária e dispensa Kafka Connect. Se a origem passar a emitir
`UPDATE` ou `DELETE`, ou se houver mais de um consumidor, o consumidor próprio
deixa de compensar (ver "Gatilho de revisão").

## Semântica de entrega

| Caminho | Semântica | Como a duplicata é evitada |
| --- | --- | --- |
| Lote (`ingerir`) | no mínimo uma vez, com efeito idempotente | versão vigente por hash no manifesto e marcador da Silver; chaves únicas na origem |
| CDC (`cdc`) | no mínimo uma vez, com efeito idempotente | `merge` por `(vistoria_id, item_codigo)` na tabela Delta |

O CDC é no mínimo uma vez porque o slot só avança depois da gravação. O
[PostgreSQL](https://www.postgresql.org/docs/16/functions-admin.html) também
avisa que a posição de um slot avançado pode voltar para um ponto anterior se o
servidor cair antes do próximo checkpoint, então a reentrega pode acontecer
mesmo sem falha do consumidor.

A chave de deduplicação é `(vistoria_id, item_codigo)`, a chave primária de
`vistoria_item`. Como a origem é insert-only, um item nunca muda depois de
inserido, e a chave basta. O LSN de fim da transação vai na coluna `lsn_origem`
para rastreio, não para deduplicar.

## Demonstração

Em ambiente de desenvolvimento com a carga concluída, pause o consumidor
contínuo para a demonstração controlar a leitura do fluxo:

```bash
docker compose stop cdc
docker compose run --rm carga demonstrar-cdc
docker compose start cdc
```

Saída de uma execução real, contra a base com o Censo 2024:

```text
## 1. Entrega normal: 3 vistorias novas na origem
Delta antes: 3078326. Lidos pelo CDC: 51. Inseridos: 51.
Delta depois: 3078377 (esperado 3078377).

## 2. Consumidor parado, 4 vistorias inseridas, consumidor religado
Com o consumidor parado: origem 3078445 itens, Delta 3078377 itens, slot reserved com 40888 bytes por entregar.
Ao religar: 2 transações, 68 itens inseridos.
Delta: 3078377 -> 3078445 (esperado 3078445).

## 3. Falha depois de gravar e antes de confirmar o slot
Depois da falha: origem 3078479 itens, Delta 3078479 itens, slot reserved com 11184 bytes por entregar.
Na retomada: 34 itens reentregues, 0 inseridos, 34 descartados pela chave (vistoria_id, item_codigo).
Delta: 3078445 -> 3078479 (esperado 3078479).

## 4. Reprocessar o mesmo intervalo de eventos duas vezes
1ª gravação: 17 recebidos, 17 inseridos.
2ª gravação: 17 recebidos, 0 inseridos.
```

A demonstração insere vistorias na origem, que é insert-only: rode em banco de
desenvolvimento.

## Ciclo de vida do slot

Um slot que ninguém consome retém WAL sem limite. Duas defesas:

- `max_slot_wal_keep_size=1GB` no serviço `banco`. Passando disso, o slot é
  invalidado (`wal_status = lost`) em vez de encher o disco, e a tabela Delta
  precisa ser refeita com `publicar-analitico --forcar`.
- `estado_do_slot` em [cdc.py](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/src/eduinfra/cdc.py) lê `wal_status`, o atraso
  em bytes e o WAL retido, e a demonstração imprime o atraso a cada etapa.

O [Debezium](https://debezium.io/documentation/reference/stable/connectors/postgresql.html)
documenta um segundo caso: se o banco recebe escrita em tabelas que o slot não
captura, o WAL cresce porque a posição do consumidor não avança. Aqui o
consumidor passa a cada 2 segundos mesmo sem eventos, mas não escreve nada que
mova o slot nesse caso. Não foi medido o crescimento do WAL com tráfego só em
outras tabelas.

## Parar e retomar

- Parar: `docker compose stop cdc`. O slot permanece e acumula WAL.
- Retomar: `docker compose start cdc`. A próxima passada entrega o que ficou
  pendente (etapa 2 da demonstração).
- Descartar o fluxo: remover o slot (`cdc.remover_slot`) e rodar
  `publicar-analitico --forcar`.

## Medição: slot lógico contra recarga incremental

O comando insere dados de teste. Pause o consumidor contínuo durante a
comparação e retome ao terminar:

```bash
docker compose stop cdc
docker compose run --rm carga comparar-ingestao
docker compose start cdc
```

A medição abaixo cobre a captura; não inclui a gravação no Delta.

| Vistorias novas | Itens | Recarga incremental (ms) | Slot lógico (ms) | Mesma contagem |
| ---: | ---: | ---: | ---: | :---: |
| 1 | 17 | 72,1 | 19,7 | sim |
| 100 | 1.700 | 27,6 | 54,6 | sim |
| 5.000 | 85.000 | 1.011,0 | 4.890,7 | sim |

Consulta sem nada novo, mediana de 20: recarga incremental 34,1 ms, slot lógico
116,3 ms.

Leitura dos números:

- A medição é de uma execução, em Docker Desktop no Windows, com a máquina
  também usada para outros serviços. Diferenças pequenas (1 vistoria) estão
  dentro do ruído.
- A recarga incremental usa `registrado_em` como marca d'água e a tabela
  `vistoria` não tem índice nessa coluna. Com 181 mil vistorias a varredura é
  barata, mas ela cresce com a tabela.
- O slot lógico, lido por SQL com decodificação em Python, ficou mais lento que
  a recarga em lote grande. A estimativa de 500 vistorias por dia não permite
  concluir um SLA de ingestão a partir desses lotes. Os 300 ms da origem são
  uma meta de consulta transacional, não de propagação ponta a ponta.
- A vantagem do CDC não está nesta tabela. Está em não depender da marca d'água
  (uma transação com `registrado_em` antigo que confirma tarde some da recarga
  incremental; o slot entrega por ordem de commit) e em entregar em segundos
  sem agendamento.

## Healthcheck do serviço

O Compose considera o serviço `cdc` saudável quando `/tmp/cdc_vivo`, que o
consumidor atualiza a cada passada, foi modificado nos últimos 60 s. O arquivo
só passa a existir ao fim da primeira passada.

Medido em 19/10/2026, no Docker Desktop (Windows), com 200 escolas geradas por
`gerar_amostra` e não com o Censo real; uma execução de cada caso:

- Com carência (`start_period`) de 30 s, o contêiner ficou `unhealthy` na
  partida e só virou `healthy` aos 84 s, porque a primeira passada levou mais
  que a carência.
- Com 120 s, foi de `starting` a `healthy` em 52 s, sem `unhealthy` e sem
  reinícios.
- Parando o contêiner, inserindo 10 itens e religando, os 15 itens inseridos
  desde a linha de base chegaram ao Delta 86 s depois do `start`, e o slot ficou
  sem atraso. Com o consumidor em execução, 5 itens chegaram em cerca de 9 s.
- Depois do `stop` e `start`, o contêiner voltou `healthy` em 15 s porque o
  arquivo de antes da parada ainda estava recente; esse estado não prova que o
  novo processo estava vivo, a entrega dos itens é que prova.

## O que não foi medido

- Debezium com Kafka Connect ou Debezium Server.
- Latência ponta a ponta do commit até a linha visível no Delta, com o
  consumidor em modo contínuo, além da medição pontual acima.
- Crescimento do WAL com o consumidor parado por horas.
- O healthcheck do serviço `cdc` com o Censo real e com o consumidor travado
  (não parado).

## Gatilho de revisão

Revisar o consumidor próprio se a origem passar a emitir `UPDATE` ou `DELETE`,
se surgir um segundo consumidor do mesmo fluxo, ou se o atraso do slot passar de
100 MB em operação normal.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Mecanismo, semântica de entrega, demonstração e medições do CDC da E2 | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 12 de outubro de 2026 |
| 1.1 | Medição do healthcheck do serviço `cdc` e do stop/start | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 19 de outubro de 2026 |
