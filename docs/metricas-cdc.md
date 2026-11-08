# Métricas do CDC

Esta página mede se o que sai da origem chega igual ao Delta, quanto tempo leva e o
que acontece quando algo quebra. O fluxo medido é `vistoria_item` até
`eventos_vistoria`, descrito em [CDC e semântica de entrega](cdc-e-semantica-de-entrega.md).

As oito métricas de consistência, latência e resiliência foram escolhidas por Artur
Mendonça Arruda antes da implementação: checksum de linhas, contagem por operação, fora de
ordem, latência ponta a ponta, atraso de captura, atraso de carga, desvio de esquema e
volume da fila de erros. O frescor, a nona, foi sugerido durante o trabalho porque a
[issue #52](https://github.com/daviegito/bd2_2026_2_eduinfra/issues/52) o exige, e Artur
aceitou. As decisões de arquitetura estão no
[ADR 0007](adr/0007-telemetria-e-fila-de-erros-do-cdc.md).

## Resultado

| Grupo | Métrica | Resultado |
| --- | --- | --- |
| Consistência | Checksum de linhas | 3.174.801 linhas iguais, 0 diferentes, 0 só na origem, 0 só no Delta |
| Consistência | Contagem por operação | 7.089 inserções lidas do WAL: 6.579 aplicadas, 510 já estavam no Delta, 0 na fila. Nenhum `UPDATE`, `DELETE` ou `TRUNCATE` |
| Consistência | Fora de ordem | 0 LSN invertido em 414 transações. Sob carga concorrente sintética, `registrado_em` inverteu em 46% a 48% das transações |
| Latência | Ponta a ponta | Mediana de 1,4 a 1,8 s no evento recente e de 4,0 a 4,1 s no atrasado |
| Latência | Atraso de captura | Mediana de 1,1 a 1,8 s, quase toda espera pelo laço de 2 s. A mediana de decodificar o WAL foi de 0,01 a 0,06 s |
| Latência | Atraso de carga | Mediana de 0,3 a 0,7 s no evento recente e de 2,5 a 2,8 s no atrasado |
| Frescor | Tempo desde o último dreno | 1,4 a 2,0 s com o consumidor ligado. Com ele parado, 259 s e a auditoria saiu com código 1 |
| Resiliência | Desvio de esquema | Nenhuma coluna das tabelas publicadas mudou desde a migração 0003. Nas falhas provocadas, coluna nova e coluna renomeada foram acusadas. Tipo alterado só tem teste unitário |
| Resiliência | Fila de erros | 0 linhas nos dados reais. Nas falhas provocadas, 51 linhas e nenhuma sem explicação |

## Como reproduzir

```bash
docker compose build carga cdc
docker compose run --rm carga migrar
docker compose up -d --no-deps cdc

# Insere vistorias de teste, mede latência e ordem e termina com a auditoria.
docker compose run --rm carga medir-cdc --repeticoes 30

# Só lê. Sai com código 1 se houver violação.
docker compose run --rm carga auditar-cdc

# Exige o consumidor parado.
docker compose stop cdc
docker compose run --rm carga medir-recarga --repeticoes 30
docker compose up -d --no-deps cdc
```

`medir-cdc` e `medir-recarga` gravam vistorias que a origem insert-only nunca mais
apaga. Rode em banco de desenvolvimento. Os cenários de falha estão em
[test_resiliencia_cdc_integracao.py](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/eaefafe/tests/test_resiliencia_cdc_integracao.py),
que cria um banco descartável a cada teste.

Para repetir as falhas no consumidor anterior à mudança, com o banco ligado e
`DATABASE_URL` exportada:

```bash
git worktree add ../eduinfra-antes fb8c926
cd ../eduinfra-antes
uv run python "$OLDPWD/docs/evidencias/metricas-cdc/falhas_antes_da_mudanca.py"
```

## Condições da medição

Duas rodadas em 31 de outubro de 2026, com 30 amostras por cenário, no Docker Desktop
(Windows), máquina virtual com 8 CPUs e 3,7 GiB. Os dados são o Censo 2024 mais as
vistorias de teste, cerca de 3,2 milhões de itens.

A máquina não estava dedicada. Os projetos `gov-bricks` (Airflow, MinIO e PostgreSQL) e
`infra` rodavam juntos, com o Airflow oscilando entre 10% e 190% de CPU, e o Windows
tinha 0,6 GB de memória livre quando conferido. As medianas se repetiram entre as
rodadas, mas os p95 e os máximos variaram muito, então trate esses dois como
indicativos. Não houve rodada com a máquina limpa.

## Consistência

### Checksum de linhas

A auditoria calcula um MD5 das colunas de cada linha, dos dois lados e no mesmo processo
DuckDB, para o formato ser idêntico. Depois compara linha a linha pela chave
`(vistoria_id, item_codigo)`.

| Caminho | Iguais | Diferentes |
| --- | ---: | ---: |
| Linha de base e recarga (sem LSN) | 3.165.961 | 0 |
| CDC (com LSN) | 8.840 | 0 |

A lista original pedia amostragem, e Artur trocou por tabela inteira depois da medição.
Comparar 3.167.729 linhas levou 17,4 s com pico de 469 MiB, duas repetições com cerca de
3,18 milhões de linhas levaram 15,8 s (460 MiB) e 41,8 s (498 MiB, saída não guardada), e o comando inteiro levou 77 s em 3.174.801 linhas, contando
a subida do contêiner. Na hora da decisão, só 0,07%
das linhas do Delta tinham vindo do CDC (2.261 de 3.167.712), então uma amostra uniforme
quase não testaria esse caminho. Uma amostra de 1% também acha uma linha errada isolada
em só 1% das vezes.

A lista falava em checksum em tempo real. Ele roda sob demanda, porque a origem é
insert-only e uma linha entregue não muda.

Uma linha só na origem é classificada como pendente no slot, parada na fila de erros ou
sem explicação. Só a última indica perda. A ordem de leitura evita alarme falso com o
consumidor ligado: a origem é lida primeiro, depois o slot e por último o Delta.

### Contagem por operação

| Operação | Lidas do WAL | Aplicadas no Delta | Na fila de erros | Já estavam no Delta | Pendentes no slot |
| --- | ---: | ---: | ---: | ---: | ---: |
| `INSERT` | 7.089 | 6.579 | 0 | 510 | 0 |
| `UPDATE` | 0 | 0 | 0 | 0 | 0 |
| `DELETE` | 0 | 0 | 0 | 0 | 0 |
| `TRUNCATE` | 0 | 0 | 0 | 0 | 0 |

Os 510 itens são os que a medição da recarga gravou direto no Delta com o consumidor
parado. Ao religar, ele leu os mesmos itens do WAL e o merge descartou todos pela chave.

A lista pedia cruzar operações "dentro de um mesmo intervalo de tempo". A contagem usa o
LSN, porque os dois lados têm relógios e atrasos diferentes e um intervalo de tempo não
casaria as mesmas linhas. As divergências do checksum saem agrupadas por dia de
`registrado_em`, para localizar onde estão.

Dois achados condicionaram esta métrica:

- **A publicação divergia entre bancos.** A documentação e o banco local tinham só
  `INSERT`, mas um banco criado do zero publicava as quatro operações. Duas versões da
  migração 0005 circularam com a mesma revisão, e o Alembic não percebe a diferença. Só
  com `INSERT`, um `UPDATE` por fora dos gatilhos nem chega ao slot e a contagem daria
  zero sempre. A migração 0007 alinha todos os bancos nas quatro operações.
- **`pg_stat_user_tables` não serve de prova.** Depois de uma queda do banco,
  `n_tup_ins` marcou 0 numa tabela de 3,1 milhões de linhas.

### Fora de ordem

| Eixo | O que mede | Resultado |
| --- | --- | --- |
| LSN | Inversão na entrega, que só deveria aparecer em reentrega | 0 em 414 transações |
| `registrado_em` | Transação que começou antes e confirmou depois | 57 de 120 (47,5%) e 55 de 120 (45,8%), com diferença máxima de 0,79 s |
| `ocorrido_em` | Evento mais antigo que um já entregue | Reflete o gerador, ver abaixo |

O segundo eixo é o que traz informação nova. Com 4 escritores segurando cada transação
por até 1 s, simulamos sobre os commits medidos uma recarga por marca d'água a cada 2 s:

| Recarga por marca d'água | Rodada 1 | Rodada 2 |
| --- | ---: | ---: |
| Sem margem | perde 7 de 120 | perde 6 de 120 |
| Com a margem de 10 minutos do ADR 0004 | perde 0 | perde 0 |
| Slot lógico | entregou 120 | entregou 120 |

O [ADR 0004](adr/0004-ingestao.md) escolheu o CDC por não depender da marca d'água, sem
medir esse efeito. Agora há um número, com a ressalva de que a taxa depende da carga
sintética e não prevê a de produção. A margem de 10 minutos também cobriu essa carga, ao
custo de reler 10 minutos a cada ciclo.

O atraso de `ocorrido_em` não permite conclusão: 64 transações passaram da tolerância de
7 dias, mas foi o cenário de evento atrasado que as criou, mais o resíduo dos testes. O
gatilho de 5% do ADR 0004 só pode ser avaliado com vistorias reais.

## Latência

Cada cenário é medido de dois lados: por fora, do commit até um leitor ver a vistoria no
Delta, e por dentro, pela telemetria do consumidor, com os instantes vindos do relógio do
PostgreSQL. A telemetria tem uma linha por transação e fica na tabela Delta
`telemetria_cdc`.

| Cenário | Rodada | Por fora: mediana (s) | p95 (s) | Máximo (s) | Por dentro: mediana (s) | p95 (s) | Máximo (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Evento recente | 1 | 1,80 | 9,08 | 17,18 | 1,90 | 9,06 | 18,77 |
| Evento recente | 2 | 1,41 | 4,87 | 10,22 | 1,62 | 4,94 | 10,30 |
| Evento atrasado, partição com 3 milhões de linhas | 1 | 4,09 | 9,44 | 17,22 | 4,10 | 9,68 | 17,24 |
| Evento atrasado, partição com 3 milhões de linhas | 2 | 4,01 | 10,13 | 16,20 | 4,10 | 10,08 | 16,33 |

Os dois lados coincidem em até 0,24 s nas medianas e nos p95. A medida por fora pode ficar
alguns décimos abaixo porque o consumidor só lê o relógio depois que o merge retorna. Na
rodada 1, a medida por fora ainda marcava o instante antes de atualizar a tabela, o que
também podia reduzi-la. Corrigimos isso antes da rodada 2.

Medianas de cada etapa, em segundos:

| Etapa | Recente, rodada 1 | Recente, rodada 2 | Atrasado, rodada 1 | Atrasado, rodada 2 |
| --- | ---: | ---: | ---: | ---: |
| Espera pela próxima passada | 0,79 | 1,26 | 1,47 | 1,12 |
| Leitura e decodificação do WAL | 0,02 | 0,01 | 0,06 | 0,02 |
| Conversão e cabeçalhos | 0,01 | 0,01 | 0,03 | 0,01 |
| Merge no Delta | 0,71 | 0,29 | 2,77 | 2,50 |

O que os números mostram:

- **A origem não é o gargalo.** A lista previa que atraso de captura alto indicaria gargalo
  na origem. Aqui ele é quase todo a espera pelo nosso laço de 2 s. Decodificar o WAL leva
  centésimos de segundo na mediana, com picos de até 5,73 s. Para reduzir a mediana do evento
  recente, o que importa é o intervalo do laço.
- **O dado atrasado custa no merge.** O merge na partição da linha de base leva de 2,5 a
  2,8 s, contra 0,3 a 0,7 s na partição do mês. O piloto de 3 amostras sugeria cerca de
  12 s, e essa estimativa estava alta.
- **Os picos não têm padrão por partição.** Houve merge de 16,83 s na partição do mês e de
  15,38 s na da linha de base. A conversão, que é só uma consulta curta, chegou a 3,70 s.
  Isso aponta para disputa de recursos na máquina, sem prova, porque não houve rodada
  limpa.
- **A primeira passada depois de religar é a mais cara.** Com 222 MB de WAL acumulado e 459
  itens, levou 61 s: 50,38 s de merge, 9,78 s de leitura e 0,75 s de conversão. Os 50 a 86 s
  já registrados no [healthcheck](cdc-e-semantica-de-entrega.md#healthcheck-do-servico)
  parecem vir mais do merge que do WAL. A causa do merge lento não foi investigada.
- **Arquivos pequenos não pesaram nesta escala.** Cada passada acrescenta um arquivo. A
  partição do mês foi de 72 para 109 arquivos e a da linha de base de 58 para 88, e o merge
  não piorou. Isso não prevê o que acontece com 500 arquivos por dia, a estimativa de
  vistorias diárias. Hoje não há compactação.

### Recarga periódica

A E2 pede a latência e o custo do CDC contra a recarga. Um ciclo com 1 vistoria nova levou
mediana de 0,54 s, p95 de 2,33 s e máximo de 23,42 s em 30 amostras. A latência abaixo é
derivada desse tempo, porque um commit cai em ponto uniforme do período.

| Período da recarga (s) | Latência média derivada (s) | Pior caso derivado (s) |
| ---: | ---: | ---: |
| 2 | 1,54 | 4,33 |
| 300 | 150,54 | 302,33 |
| 3600 | 1800,54 | 3602,33 |

O slot parado retém 10.692 bytes de WAL por vistoria. Com a estimativa de 500 vistorias por
dia, o limite de 1 GB chega em 200,8 dias. Esse prazo vale só para o tráfego de vistorias:
outras escritas no banco também contam, e os testes e medições acumularam 222 MB em cerca
de 40 minutos.

## Frescor

Frescor é o tempo desde o início da última passada concluída sem deixar nada para trás,
porque tudo o que confirmou antes desse instante já está legível. Medir "agora menos a
última linha" não serviria: com cerca de 500 vistorias por dia (estimativa), a madrugada
sem vistorias ficaria igual a um consumidor morto.

| Situação | Frescor (s) | Código de saída de `auditar-cdc` |
| --- | ---: | :---: |
| Consumidor ligado, três auditorias | 1,4 a 2,0 | 0 |
| Consumidor parado | 259,0 | 1 |

O SLA proposto é de 60 s para `eventos_vistoria`, ajustável com `--sla-frescor`. É o mesmo
limite do healthcheck e cerca de três vezes a pior latência ponta a ponta em regime
(18,77 s). Uma reinicialização do consumidor pode violar esse SLA, como a primeira passada
de 61 s, e isso é de propósito, porque nesse tempo o dado de fato não chega.

Isto cobre só o caminho do CDC. A issue #52 pede o frescor da tabela que alimenta o painel,
que depende do [modelo dimensional](https://github.com/daviegito/bd2_2026_2_eduinfra/pull/55)
e ainda não existe na `main`, então esta medição não fecha a issue.

## Resiliência

A origem aceita linhas que o consumidor não converte, e nem todas dependem de mudança de
esquema. Estes casos foram provocados num banco descartável, antes e depois da mudança:

| Falha provocada | Antes | Agora |
| --- | --- | --- |
| `ADD COLUMN` com valor preenchido | Valor perdido, sem aviso | Linha entregue sem a coluna nova, com alerta na telemetria e violação na auditoria |
| `RENAME COLUMN` | `KeyError` em toda passada. Desfazer a migração não destrava, porque o WAL é decodificado com o catálogo da época | Linhas na fila, o slot avança e as seguintes chegam |
| `ocorrido_em = '-infinity'` ou ano 4000 a.C. | `DataError` em toda passada, e nada depois dessa linha chega ao Delta | Linha na fila, e a vizinha na mesma passada é entregue |
| `UPDATE` com `session_replication_role = replica` | `ValueError` em toda passada, ou invisível se a publicação fosse só `INSERT` | Contado, registrado na fila, e o checksum acusa a linha diferente |

Com todas essas falhas juntas, a auditoria saiu com código 1:

```text
Só na origem: 34 (0 pendentes no slot, 34 na fila de erros, 0 sem explicação). Só no Delta: 0.
Fila de erros: 51 linhas (update, violacao_insert_only: 17, insert, KeyError: 17, insert, DataError: 17).
Desvios de esquema vistos no WAL: vistoria_item.qtd: coluna_nova (1x), vistoria_item.quantidade: coluna_ausente (1x), vistoria_item.coluna_nova: coluna_nova (2x).

- 17 linhas com conteúdo diferente entre a origem e o Delta
- 34 itens parados na fila de erros
- 17 alterações desviaram do insert-only
- esquema fora do contrato: vistoria_item.coluna_nova: coluna_nova
```

A fila guarda só a chave e o motivo. A origem é insert-only e continua com o dado, e
`vistoria.observacao` é texto livre que pode conter dado pessoal.

Durante a medição, o Windows entrou em modo de espera por cerca de 5 minutos e o PostgreSQL
passou por recuperação de falha. A auditoria seguinte fechou com 0 linhas diferentes e 0
sem explicação. Foi um único evento acidental, com o consumidor parado.

O desvio de tipo e o estouro de inteiro têm só teste unitário: a view `escola_estado_atual`
depende de `vistoria_item.quantidade`, então `ALTER COLUMN ... TYPE` exigiria remover a view
antes.

## Recuperação e tratamento da fila (issue #58)

A fila `fila_de_erros_cdc` é um histórico: nenhuma entrada é apagada ou alterada.
Os recibos ficam em uma tabela Delta separada, `tratamentos_cdc`, identificados
por **LSN + chave completa da ocorrência**. O primeiro recibo registra ação,
identificador operacional (`operador-01`), instante UTC, motivo padronizado,
chaves revisadas e impressões dos estados analíticos, sem nomes, e-mails,
observação da vistoria ou texto livre. Repetir o comando preserva esse recibo.
O identificador é informado por quem executa; ele não é uma autenticação.

### Reprocessar itens rejeitados

Primeiro corrija a causa operacional (por exemplo, restaure o contrato de uma
coluna renomeada). Em seguida:

```bash
docker compose run --rm carga reprocessar-fila-cdc \
  --responsavel operador-01 --motivo esquema_corrigido
```

O comando busca cada item pela chave primária na origem, sem publicar a linha
de base inteira. O merge insere apenas itens ausentes; uma linha já existente
com conteúdo diferente não é sobrescrita nem considerada recuperada. O recibo
só é gravado depois de confirmar o conteúdo no Delta. Se houver falha entre a
escrita e o recibo, repetir é seguro. Datas ainda inválidas e itens ausentes na
origem permanecem pendentes; o comando retorna `1` se alguma tentativa ficar
pendente. Retorna `0` se todas as entradas selecionadas forem recuperadas ou
já tiverem recibo. Entradas de UPDATE/DELETE/TRUNCATE exigem tratamento manual
separado e continuam sendo acusadas pela auditoria.

Para selecionar apenas uma ocorrência, use `--lsn <decimal> --chave <chave>`
juntos. Para obter esses valores sem imprimir o conteúdo das linhas:

```bash
docker compose run --rm -T --entrypoint python carga - <<'PYTHON'
from eduinfra.configuracao import carregar_configuracao
from eduinfra.fila_de_erros import ler
for entrada in ler(carregar_configuracao()).to_pylist():
    print(entrada["lsn_origem"], entrada["chave"], entrada["operacao"])
PYTHON
```

### Revisar um desvio do insert-only

Uma alteração indevida não é automaticamente propagada ao Delta. Depois de
revisar a ocorrência e decidir aceitar explicitamente aquela divergência:

```bash
docker compose run --rm carga tratar-desvio-cdc \
  --lsn <decimal> --chave <chave> \
  --responsavel operador-01 --motivo desvio_revisado
docker compose run --rm carga auditar-cdc
```

O aceite se refere à ocorrência exata e ao estado analítico observado. A auditoria
mantém o histórico das operações e informa os tratamentos e as chaves aceitas
fora das contagens de divergência. Novos LSNs continuam pendentes, mesmo para
a mesma chave. Se o estado de uma chave revisada mudar, o aceite anterior não
suprime seu checksum. Inserções legítimas de outras chaves não são suprimidas
nem invalidam o aceite das chaves anteriores. Uma entrada de INSERT rejeitado
não pode ser encerrada por aceite manual.

UPDATE/DELETE sem chave suficiente são recusados. Para TRUNCATE, o escopo revisado
inclui as chaves existentes na origem e no Delta; a comparação pode custar uma
leitura ampla, enquanto a recuperação de INSERT continua seletiva. O tratamento
não dispensa problemas de contrato no catálogo, slot inválido, frescor estourado,
pendências novas ou divergências não revisadas. O código `0` só volta se não houver
outras violações. Execute os comandos de tratamento em série para evitar disputa
na criação inicial das tabelas Delta; conflitos de escrita exigem nova tentativa.

Os motivos permitidos são `esquema_corrigido`, `falha_transitoria_resolvida`,
`desvio_revisado` e `legado_revisado`. O tratamento não corrige a origem insert-only,
não reconstrói valores apagados e não recupera automaticamente todos os erros.
As [evidências da #58](evidencias/issue-58/reprocessamento.md) registram os testes
e a comparação de custo com a carga completa.

## Limites

- A origem aceita `-infinity` e datas antes de Cristo, porque o check só exige
  `ocorrido_em <= registrado_em`. A fila contém o dano, mas a validação na origem continua
  aberta.
- A recuperação da fila é operacional e seletiva, pelos comandos acima. Dados
  ainda inválidos permanecem pendentes; o aceite de desvios exige revisão explícita.
  A validação preventiva das datas e da publicação completa está no PR #67 (#57).
- O aceite manual de um desvio conserva a divergência revisada e seu histórico;
  o operador assume explicitamente esse estado. Ele não altera a origem ou o Delta.
- A taxa de entrega sob carga sustentada e o crescimento de arquivos ao longo de meses não
  foram medidos.
- Os testes de integração não rodam em CI: o único fluxo do repositório publica a
  documentação.

## Evidências

Saídas brutas dos comandos, sem alteração. Nenhuma traz valor de linha da origem.

| Arquivo | O que mostra |
| --- | --- |
| [medicao_1.txt](evidencias/metricas-cdc/medicao_1.txt) e [medicao_2.txt](evidencias/metricas-cdc/medicao_2.txt) | As duas rodadas de `medir-cdc` |
| [recarga.txt](evidencias/metricas-cdc/recarga.txt) | `medir-recarga` e o WAL retido por vistoria |
| [auditoria_parado.txt](evidencias/metricas-cdc/auditoria_parado.txt) | `auditar-cdc` com o consumidor parado, código de saída 1 |
| [auditoria_pos_queda.txt](evidencias/metricas-cdc/auditoria_pos_queda.txt) | `auditar-cdc` depois da recuperação de falha do PostgreSQL |
| [auditoria_final.txt](evidencias/metricas-cdc/auditoria_final.txt) | Última auditoria: 3.175.243 linhas iguais, 0 diferentes |
| [falhas_antes_da_mudanca.txt](evidencias/metricas-cdc/falhas_antes_da_mudanca.txt) | Saída de [falhas_antes_da_mudanca.py](evidencias/metricas-cdc/falhas_antes_da_mudanca.py) no commit `fb8c926` |
| [falhas_depois_da_mudanca.txt](evidencias/metricas-cdc/falhas_depois_da_mudanca.txt) | Saída de [falhas_depois_da_mudanca.py](evidencias/metricas-cdc/falhas_depois_da_mudanca.py) no consumidor atual |
| [custo_do_checksum.txt](evidencias/metricas-cdc/custo_do_checksum.txt) | Saída de [custo_do_checksum.py](evidencias/metricas-cdc/custo_do_checksum.py), origem dos 17,4 s e 469 MiB, e [a repetição de 15,8 s](evidencias/metricas-cdc/custo_do_checksum_repeticao.txt), cujas 306 linhas só na origem estavam pendentes no slot, com o consumidor parado |

Os scripts de falhas criam e removem um banco próprio. O de custo do checksum só lê, e roda
no contêiner porque usa `resource`:

```bash
docker compose run --rm -T -e LINHA_A_LINHA=1 --entrypoint uv carga run --no-sync python - < docs/evidencias/metricas-cdc/custo_do_checksum.py
```

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Medição das métricas de consistência, latência, frescor e resiliência do CDC | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 31 de outubro de 2026 |
| 1.1 | Inclui as saídas brutas e os scripts das falhas provocadas | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 31 de outubro de 2026 |
