# Frescor e SLA da Gold

A tabela que alimenta o painel é `gold.fato_infra_escola_ano`, o perfil de
infraestrutura de cada escola por ano. O **frescor** dela é há quanto tempo a
vistoria mais antiga que já está na origem, mas ainda não chegou à Gold, está
esperando. O **SLA** é de **25 horas**. Cada medição fica registrada em
`monitoramento.frescor_gold`, e `medir-frescor` sai com código 1 quando o SLA é
violado. Responde à issue #52.

## O que se mede

A Gold é reconstruída inteira por `construir-gold`, numa transação só, a partir de
uma versão da fonte canônica de eventos ([modelo analítico](modelo-analitico.md)).
O que a origem recebe depois só aparece na Gold quando chega à fonte, pelo CDC ou
pelas janelas, e a Gold é construída de novo.

| Medida | Definição |
| --- | --- |
| Vistorias pendentes | vistorias da origem com pelo menos um item que falta em `gold.fato_vistoria_item`, pela chave `(vistoria_id, item_codigo)` |
| Frescor | agora menos o `registrado_em` da pendente mais antiga; **zero** quando não há pendente |
| Dentro do SLA | frescor menor ou igual ao SLA, com a Gold construída |

Três escolhas:

- **Não é "agora menos a última construção".** Num dia sem vistorias, a Gold
  continua completa, e essa conta acusaria atraso sem haver dado faltando. O
  frescor só cresce quando existe dado na origem que o painel ainda não mostra.
- **O fato fino basta.** Os dois fatos saem da mesma fonte de eventos, na mesma
  transação, e o fato anual forma os anos a partir desses eventos
  ([ADR 0008](adr/0008-fonte-canonica-de-eventos.md)). Uma vistoria cujos itens
  estão no fato fino tem o ano dela no fato anual. A comparação é pela chave do
  evento, e não só pela vistoria: com a fonte em Delta, a entrega pode ser parcial,
  com um item aplicado e outro na fila de erros do CDC. Vistoria sem item não é evento:
  nunca chega à Gold e não conta. A versão 1.1 contava a vistoria sem item de um
  ano ausente do fato anual, quando ele formava os anos a partir de todas as
  vistorias da origem; com a fonte de eventos, contá-la deixaria o SLA violado
  para sempre.
- **O relógio é o do banco**, o mesmo que carimba `registrado_em`. Como
  `registrado_em` é o início da transação que gravou a vistoria, o frescor medido
  nunca é menor que o real.

Gold que não existe conta como violação: o painel não tem o que mostrar.

## De qual caminho o SLA fala

A Gold lê a fonte canônica de eventos, a tabela Delta `eventos_vistoria`, que o
CDC e as janelas de dado atrasado completam
([ADR 0008](adr/0008-fonte-canonica-de-eventos.md)). As pendentes continuam sendo
contadas contra a origem. Por isso o SLA cobre o trecho inteiro **origem → fonte
→ Gold**: um CDC parado aparece aqui como atraso, qualquer que seja o caminho que
trouxe o dado à origem:

- **Vistorias do dia a dia**, registradas na origem: entram no painel em até 25 h.
- **Censo anual**, carregado pelo lote (`ingerir`): o prazo de 25 h começa quando a
  edição entra na origem. O atraso entre o INEP publicar e a Squad ingerir é do
  lote, registrado no manifesto `ingestao_execucao`, e não deste SLA.

O frescor do CDC, que leva as vistorias à tabela Delta `eventos_vistoria` com SLA
de 60 s, é outra medição, do PR #59 e do ADR 0007. Ela mede só o primeiro trecho;
este SLA inclui esse trecho e a construção da Gold.

## O SLA de 25 horas

- **Uma construção por dia basta para a pergunta do painel.** O painel responde se
  a infraestrutura da escola se reflete no ENEM, com dados anuais. Uma vistoria que
  aparece no dia seguinte não muda nenhuma decisão.
- **Construir mais vezes custa caro.** Cada construção recria a Gold inteira: 60,3,
  85,8 e 69,3 s nas três execuções medidas, e cerca de 1 GB de WAL por execução
  (issue #60), que o slot do CDC precisa reter.
- **A hora a mais é folga para a construção e um retry.** Com a construção diária
  num horário fixo, a vistoria registrada logo depois espera quase 24 h. A margem
  cobre a construção e uma nova tentativa se a primeira falhar.

O valor é ajustável com `medir-frescor --sla-horas`.

## Registro e falha

```bash
docker compose run --rm carga construir-gold   # constrói e registra uma medição
docker compose run --rm carga medir-frescor    # registra; código 1 fora do SLA
```

- `construir-gold` registra o frescor ao fim de cada construção. Se houver violação,
  ela vai para o log como erro, mas o comando não falha: a construção deu certo.
- `medir-frescor` registra, imprime o resultado e **sai com código 1** se o SLA for
  violado. É o comando que a orquestração (issue #50) deve agendar depois de cada
  construção, para a falha aparecer como falha da execução.

O registro fica fora do schema `gold`, que é apagado a cada construção. O comando
cria `monitoramento.frescor_gold` na primeira medição, como `construir-gold` cria o
schema `gold`: são dados derivados, não da origem, e por isso ficam fora das
migrações.

```sql
select medido_em, comando, vistorias_pendentes, frescor_s, sla_s, dentro_do_sla
from monitoramento.frescor_gold
order by id desc
limit 10;
```

## Demonstração

```bash
docker compose run --rm carga construir-gold
docker compose run --rm carga demonstrar-frescor
docker compose run --rm carga medir-frescor --sla-horas 0.0005; echo $?
docker compose run --rm carga processar-janelas   # dispensável com o serviço cdc no ar
docker compose run --rm carga construir-gold
docker compose run --rm carga medir-frescor; echo $?
```

`demonstrar-frescor` insere uma vistoria na origem, que é insert-only: ela fica no
banco, marcada na observação. Rode num ambiente de desenvolvimento. Para não esperar
25 h, a violação usa um SLA reduzido a segundos.

Saída real em 02/11/2026, sobre o banco carregado com o Censo 2024 (201.128
vistorias, incluindo as de testes anteriores):

```text
# Frescor da Gold e violação do SLA

## 1. Estado de partida, SLA de 25,0 h

em dia, nenhuma vistoria da origem fora da Gold (SLA de 25,0 h)

## 2. Uma vistoria nova na origem, sem reconstruir a Gold

1 vistoria(s) fora da Gold, a mais antiga esperando há 0,0 s: dentro do SLA de 25,0 h

## 3. 5 s depois, com o SLA reduzido a 2,0 s para a demonstração

1 vistoria(s) fora da Gold, a mais antiga esperando há 5,6 s: FORA DO SLA de 2,0 s
`medir-frescor` com esse SLA sai com código 1.
```

Em seguida, pela CLI (linhas de log resumidas):

```text
$ eduinfra medir-frescor --sla-horas 0.0005
1 vistoria(s) fora da Gold, a mais antiga esperando há 8,9 s: FORA DO SLA de 1,8 s
código de saída: 1

$ eduinfra construir-gold
INFO gold construída em 69.3 s
INFO frescor da Gold: em dia, nenhuma vistoria da origem fora da Gold (SLA de 25,0 h)

$ eduinfra medir-frescor
em dia, nenhuma vistoria da origem fora da Gold (SLA de 25,0 h)
código de saída: 0
```

E o que ficou registrado em `monitoramento.frescor_gold`, da vistoria inserida em
diante:

| Comando | Pendentes | Frescor (s) | SLA (s) | Dentro do SLA |
| --- | ---: | ---: | ---: | :---: |
| `demonstrar-frescor` | 1 | 0,0 | 90.000 | sim |
| `demonstrar-frescor` | 1 | 5,6 | 2 | não |
| `medir-frescor --sla-horas 0.0005` | 1 | 8,9 | 1,8 | não |
| `medir-frescor` | 1 | 12,3 | 90.000 | sim |
| `construir-gold` | 0 | 0,0 | 90.000 | sim |
| `medir-frescor` | 0 | 0,0 | 90.000 | sim |

A consulta das pendentes levou de 0,73 a 0,79 s sobre 201.251 vistorias (`EXPLAIN
ANALYZE`, três execuções, com a Gold recém-construída). Escrita com `or` num `where`
só, ela levava 1,8 s; por isso as duas condições ficam separadas num `union`. O
comando inteiro leva cerca de 4 s, quase tudo na partida do Python.

## O que se perde

- **A violação de 25 h não foi esperada de verdade.** A demonstração reduz o SLA a
  segundos. A regra é a mesma, mas o valor real só será exercitado com a
  construção agendada.
- **Uma tabela monitorada.** O SLA fala de `fato_infra_escola_ano`. As outras
  tabelas da Gold saem do mesmo retrato e herdam o mesmo frescor, mas não há
  medição separada para elas.
- **A consulta segue a forma dos fatos.** Ela sabe que o fato fino tem uma linha
  por item de vistoria e que o fato anual sai dos mesmos eventos. Se a Gold mudar
  a regra de formação das linhas, a consulta das pendentes muda junto.
- **O frescor depende de `registrado_em`.** A consulta das pendentes compara a
  origem com o fato fino, e não com a fonte de eventos. Um item parado na fila de
  erros do CDC deixa a vistoria pendente, e o SLA violado, até ser reprocessado
  (issue #58) ou completado por `processar-janelas`.
- **A consulta ficou mais cara.** Comparar pela chave levou de 1,7 a 1,9 s com o
  Censo 2024, contra 0,25 s comparando só pela vistoria, que não via a entrega
  parcial.

## Quando revisar

- Se o painel passar a ser usado no dia a dia da secretaria, e não só no
  planejamento: o SLA de 25 h fica longo, e a Gold precisa de construção
  incremental.
- Se a construção passar de 1 hora: a folga do SLA deixa de cobrir um retry.
- Se a orquestração (issue #50) agendar a construção com outra frequência: o SLA
  acompanha a frequência mais a margem.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Frescor da Gold, SLA de 25 h, registro e demonstração da violação | [Gabriel Lopes de Amorim](https://github.com/BrzGab) |  | 2 de novembro de 2026 |
| 1.1 | Conta como pendente a vistoria sem item de escola e ano que faltam no fato anual | [Gabriel Lopes de Amorim](https://github.com/BrzGab) | [Pedro Sanchez](https://github.com/PedroDev-sketch) | 4 de novembro de 2026 |
| 1.2 | Gold lida da fonte única de eventos (ADR 0008): o SLA cobre origem → fonte → Gold, e a vistoria sem item deixa de contar | Squad EduInfra |  | 8 de novembro de 2026 |
