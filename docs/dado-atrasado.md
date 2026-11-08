# Dados atrasados e reprocessamento

Os eventos de vistoria chegam à camada analítica em **janelas mensais pelo mês
de `ocorrido_em`**, a data em que a realidade mudou na escola. Uma vistoria
registrada com atraso cai no mês em que aconteceu, nunca no mês em que o sistema
soube dela. Responde à issue #27.

Desde o [ADR 0008](adr/0008-fonte-canonica-de-eventos.md), a janela é a partição
`mes_ocorrencia` da **fonte canônica de eventos**, a tabela Delta
`warehouse/eventos_vistoria`. O CDC e as janelas gravam nela, pela mesma chave
`(vistoria_id, item_codigo)`. Um evento que chega pelos dois caminhos aparece uma
vez, e a Gold lê só essa tabela.

## As decisões

### 1. A janela é o mês de `ocorrido_em`

A origem guarda duas datas por vistoria (ADR 0001). `ocorrido_em` é quando a
escola mudou; `registrado_em` é quando o dado entrou no banco.

A pergunta de gestão é temporal: a escola tinha laboratório na época da prova?
Ela só tem resposta se o evento estiver no mês em que aconteceu. Janela por
`registrado_em` colocaria uma vistoria feita em agosto e digitada em outubro no
mês de outubro, e a série mostraria uma mudança que não aconteceu em outubro.

Os dois caminhos calculam o mês do mesmo jeito, a partir de `ocorrido_em` em UTC:
o consumidor do CDC ao montar o evento e a leitura da origem em
`eventos.LINHA_DE_BASE`. Por isso o evento atrasado cai na mesma partição, venha
de onde vier.

`registrado_em` continua tendo um papel: é a **marca d'água** das janelas. Cada
execução pega as vistorias registradas depois da anterior e descobre quais meses
elas tocam. A marca diz *o que é novo*; `ocorrido_em` diz *onde o novo entra*.

### 2. Janela mensal, aberta até 7 dias depois do fim do mês

- **Mensal** porque a escrita esperada do aplicativo é de cerca de 500
  vistorias por dia (estimativa da caracterização da E1): em torno de 15 mil
  vistorias e 250 mil itens por mês. Janela diária geraria um arquivo pequeno
  por dia, que é o problema dos arquivos pequenos.
- **Tolerância de 7 dias** porque é o prazo razoável para um técnico digitar
  uma vistoria feita em campo, e porque o planejamento da secretaria olha o mês
  depois que ele termina. A constante é `TOLERANCIA` em `src/eduinfra/janelas.py`.

### 3. O que chega depois da tolerância reabre a janela, e fica registrado

Havia três destinos possíveis para um evento que chega com a janela já fechada:

| Destino | Por que não, ou por que sim |
| --- | --- |
| Descartar | Perde dado que a origem tem. A camada analítica passaria a ter menos que a origem, e a diferença só cresceria. |
| Guardar à parte | Cada consumidor teria que lembrar de somar a área "atrasados" ao mês certo. Na prática, alguém esquece. |
| **Corrigir a janela e registrar a reabertura** | Escolhido. O evento entra na partição do mês em que ocorreu. O registro avisa quem já usou o número do mês fechado que ele mudou. |

A reabertura fica no controle das janelas, com o mês, quando foi reaberta,
quantos eventos chegaram e o maior atraso. O processamento também emite um aviso
no log. Ela é registrada mesmo quando o CDC entregou o evento antes, porque a
regra olha `registrado_em`, e não o caminho.

### 4. Reprocessar é reconciliar o mês com a origem

As janelas não têm destino próprio. Reconciliar uma janela lê o mês da origem e
compara a **impressão** dele com a da partição na fonte canônica. Se forem
diferentes, grava pelo mesmo merge do CDC, que só insere a chave que falta. Se
forem iguais, não grava nada: cada merge cria uma versão nova da tabela Delta.

A impressão é a soma exata, em 64 bits, do md5 de cada linha. Ela não depende da
ordem e muda se qualquer valor mudar. `lsn_origem` fica de fora, porque distingue
os caminhos em vez de descrever o evento. O resultado da janela diz se ela
**confere com a origem**. Uma primeira versão somava o md5 de 128 bits; a soma
virava ponto flutuante e o mês do Censo não conferia com ele mesmo.

Rodar duas vezes dá o mesmo resultado, porque a origem é insert-only e o merge
só insere o que falta.

### 5. A margem da marca d'água

`registrado_em` recebe `now()`, que é a hora do **início** da transação, não a do
commit. Uma transação longa pode confirmar depois da execução, com
`registrado_em` anterior à marca. Por isso cada execução também relê os 10
minutos anteriores à marca (`MARGEM_DA_MARCA`).

Para não contar duas vezes o que a margem traz de volta, o controle guarda os ids
das vistorias que a execução anterior viu na margem, em faixas de ids
consecutivos (a carga do Censo cabe numa faixa só). Não dá para perguntar isso à
fonte canônica: o CDC pode ter entregado a vistoria antes, sem que nenhuma
execução das janelas a tivesse visto.

O furo da margem deixou de ser um risco de perda. O CDC captura pela ordem de
commit, e a vistoria que escapar da margem chega por ele. As janelas são a rede
de segurança do CDC, e não o contrário.

## Onde ficam os dados

```
dados/warehouse/
  eventos_vistoria/                       fonte canônica, em Delta
    _delta_log/
    mes_ocorrencia=2024-05/...
    mes_ocorrencia=2026-09/...
  eventos_vistoria_janelas.json           marca d'água, vistas na margem, janelas e reaberturas
```

Cada linha é um item de uma vistoria: `vistoria_id`, `item_codigo`,
`co_entidade`, `sg_uf`, `origem`, `ocorrido_em`, `registrado_em`, `situacao`,
`quantidade`, `mes_ocorrencia` e `lsn_origem`, preenchido quando o evento veio
pelo CDC. O técnico não sai da origem.

O antigo destino em Parquet, `analitico/eventos_vistoria/`, não é mais escrito
nem lido e pode ser apagado.

## Como usar

```bash
docker compose run --rm carga processar-janelas            # reconcilia o que foi registrado desde a última vez
docker compose run --rm carga reprocessar-janela --mes 2024-05
docker compose run --rm carga demonstrar-atraso            # a demonstração abaixo
```

A demonstração insere duas vistorias na origem. Como a origem é insert-only,
elas ficam lá, marcadas na observação. Rode num ambiente de desenvolvimento.

### Testes

Os testes sem banco rodam direto:

```bash
uv run pytest tests/test_janelas.py tests/test_fonte_de_eventos.py
```

O de integração da fonte única cria um banco e um slot próprios. Ele mostra o
mesmo evento chegando pelos dois caminhos, nas duas ordens, e um evento
atrasado:

```bash
docker compose up -d banco
set -a && source .env && set +a              # exporta DATABASE_URL
uv run pytest tests/test_fonte_de_eventos_integracao.py tests/test_janelas_integracao.py
```

`tests/test_janelas_integracao.py` usa o banco carregado e, como a demonstração,
insere vistorias de teste na origem.

## Demonstração

Saída real de `demonstrar-atraso` em 08/11/2026, sobre o banco carregado com o
Censo 2024, com a fonte canônica já publicada (`publicar-analitico`) e o serviço
`cdc` parado:

```
## 1. Estado de partida

marca d'água: 2026-09-30T17:55:34.901865+00:00 -> 2026-09-30T17:55:34.901865+00:00
vistorias novas desde a marca: 0
itens que a fonte canônica ainda não tinha (o CDC não entregara): 0

## 2. Vistoria da escola 53002580 ocorrida há 3 dias (2026-11-05), registrada agora

marca d'água: 2026-09-30T17:55:34.901865+00:00 -> 2026-11-08T21:38:49.544766+00:00
vistorias novas desde a marca: 1
itens que a fonte canônica ainda não tinha (o CDC não entregara): 17
  janela 2026-11: 17 linhas, impressão 8972aabef25abafce, confere com a origem
  vistoria 362131: ocorreu 2026-11-05, registrada 2026-11-08, atraso 3.0 dias -> janela 2026-11 (dentro da tolerância)

## 3. Vistoria ocorrida em 2026-09-15, com a janela 2026-09 fechada desde 2026-10-08, registrada agora

marca d'água: 2026-11-08T21:38:49.544766+00:00 -> 2026-11-08T21:38:59.514943+00:00
vistorias novas desde a marca: 1
itens que a fonte canônica ainda não tinha (o CDC não entregara): 17
  janela 2026-09: 17 linhas, impressão 85822fadc2cb477ff, confere com a origem
  vistoria 362132: ocorreu 2026-09-15, registrada 2026-11-08, atraso 54.9 dias -> janela 2026-09 (FORA da tolerância: janela reaberta)

## 4. Reprocessar a janela 2026-11 duas vezes

1ª: 17 linhas, impressão 8972aabef25abafce
2ª: 17 linhas, impressão 8972aabef25abafce
idênticas: sim

## 5. Janelas na fonte canônica de eventos

- 2024-05: 3078105 linhas
- 2026-09: 17 linhas
- 2026-11: 17 linhas
```

Em seguida, uma passada do CDC (`docker compose run --rm carga cdc`) leu as duas
transações:

```
ResultadoDaPassada(transacoes=2, vistorias_vistas=2, itens_lidos=34, itens_inseridos=0, ...)
```

O que ela mostra:

- A vistoria de três dias atrás entrou em **novembro**, o mês em que ocorreu,
  dentro da tolerância.
- A vistoria de setembro chegou 54,9 dias depois de ocorrer, com setembro fechado
  desde 08/10. Ela entrou na partição de **setembro** e **reabriu** a janela, e a
  reabertura ficou registrada no controle.
- Com o CDC parado, as janelas gravaram os 34 itens. O CDC, ao voltar, leu os
  mesmos 34 e inseriu **zero**: o evento aparece uma vez.
- Reprocessar novembro duas vezes deu a mesma impressão.
- A `construir-gold` seguinte conferiu 3.078.139 eventos na fonte e na origem,
  sem nenhum só de um lado.

## Medições

Docker sobre WSL2, banco com o Censo 2024, em 08/11/2026:

| Operação | Tempo |
| --- | ---: |
| Primeira reconciliação (`processar-janelas`), mês 2024-05 com 3.078.105 linhas, já presente | 18,6 s |
| Reconciliação sem nada novo | 5,4 s |
| Demonstração completa (passos 1 a 4) | 22,2 s |
| Ler um mês da origem e calcular a impressão: vazio / 2024-05 | 1,3 s / 3,4 s |

Os tempos incluem o início do contêiner. Cada reconciliação de mês carrega a
extensão `postgres` do DuckDB, cerca de 1,3 s, e por isso a demonstração ficou
mais lenta que a versão em Parquet (3,7 s).

## O que se perde

- **Números de mês fechado podem mudar.** Quem publicou o total de agosto em
  setembro pode ver o número mudar em outubro. A reabertura registrada é o aviso,
  mas o consumidor precisa olhar para ela.
- **Reconciliar custa ler o mês inteiro.** Um único evento atrasado em 2024-05
  faz a janela ler e comparar 3 milhões de linhas da origem. Só grava se faltar
  algo.
- **O controle vive num arquivo.** `eventos_vistoria_janelas.json` é gravado por
  troca atômica, mas não é transacional com a tabela Delta. Se o arquivo se
  perder, a próxima execução trata tudo como carga inicial e reconcilia todos os
  meses: fica certo, só mais caro. A reabertura de um mês que ninguém registrou
  nessa execução se perde, porque a primeira execução não conta atraso.
- **Concorrência com o CDC.** Dois merges no mesmo mês ao mesmo tempo entram em
  conflito no commit. O perdedor tenta de novo, até cinco vezes; depois disso,
  falha, e a próxima execução refaz.

## Quando revisar

- Se reconciliar uma janela passar de 1 minuto, ou a janela mais pesada passar
  de 1 GB de memória: particionar também por UF dentro do mês.
- Se a fração de eventos fora da tolerância passar de 5% num mês: a tolerância de
  7 dias está curta para o ritmo real de digitação.
- Se mais de 1% dos eventos de um mês forem completados pelas janelas, e não
  pelo CDC: o CDC está perdendo eventos (ADR 0008).

## Decisão de ingestão

O [ADR 0004](adr/0004-ingestao.md) consolida a escolha de lote e CDC. O
[ADR 0008](adr/0008-fonte-canonica-de-eventos.md) substitui a coexistência das
janelas em Parquet com o CDC em Delta por uma fonte única.
