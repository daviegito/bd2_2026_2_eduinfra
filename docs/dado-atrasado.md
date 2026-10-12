# Dado atrasado e reprocessamento de janela

Os eventos de vistoria chegam à camada analítica em **janelas mensais pelo mês
de `ocorrido_em`**, a data em que a realidade mudou na escola. Uma vistoria
registrada com atraso cai no mês em que aconteceu, nunca no mês em que o sistema
soube dela. Reprocessar uma janela regrava a partição inteira a partir da
origem, e rodar duas vezes dá o mesmo resultado. Responde à issue #27.

## As decisões

### 1. A janela é o mês de `ocorrido_em`

A origem guarda duas datas por vistoria (ADR 0001). `ocorrido_em` é quando a
escola mudou; `registrado_em` é quando o dado entrou no banco.

A pergunta de gestão é temporal: a escola tinha laboratório na época da prova?
Ela só tem resposta se o evento estiver no mês em que aconteceu. Janela por
`registrado_em` colocaria uma vistoria feita em agosto e digitada em outubro no
mês de outubro, e a série mostraria uma mudança que não aconteceu em outubro.

`registrado_em` continua tendo um papel: é a **marca d'água**. Cada execução
pega as vistorias registradas depois da anterior e descobre quais meses elas
tocam. A marca diz *o que é novo*; `ocorrido_em` diz *onde o novo entra*.

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
| **Corrigir a janela e registrar a reabertura** | Escolhido. A origem é insert-only e guarda tudo, então a janela pode ser recalculada a qualquer momento. O registro avisa quem já usou o número do mês fechado que ele mudou. |

A reabertura fica em `_controle.json`, com o mês, quando foi reaberta, quantos
eventos chegaram e o maior atraso. O processamento também emite um aviso no log.

### 4. Reprocessar é regravar a partição inteira

A janela é recalculada da origem, ordenada por `vistoria_id` e `item_codigo`, e
gravada numa pasta temporária. Depois, um `os.replace` troca a partição de uma
vez: quem lê vê a versão antiga ou a nova, nunca uma escrita pela metade. Se a
conversão falhar, a partição anterior continua intacta (há teste para isso).

Cada janela tem uma **impressão**: a soma dos hashes de todas as linhas. Ela não
depende da ordem física no arquivo e muda se qualquer valor mudar. É assim que
se confere, com um número, que reprocessar duas vezes deu o mesmo resultado.

### 5. A margem da marca d'água

`registrado_em` recebe `now()`, que é a hora do **início** da transação, não a do
commit. Uma transação longa pode confirmar depois da execução, com
`registrado_em` anterior à marca, e escaparia. Por isso cada execução também
relê os 10 minutos anteriores à marca (`MARGEM_DA_MARCA`). Para a margem não
regravar janelas à toa, só conta como novo o evento cujo `vistoria_id` ainda não
está na partição do seu mês.

## Onde ficam os dados

```
dados/analitico/eventos_vistoria/
  _controle.json                    marca d'água, impressão de cada janela, reaberturas
  ocorrido_mes=2024-05/eventos.parquet
  ocorrido_mes=2026-08/eventos.parquet
  ocorrido_mes=2026-10/eventos.parquet
```

Cada linha é um item de uma vistoria: `vistoria_id`, `co_entidade`, `origem`,
`ocorrido_em`, `registrado_em`, `item_codigo`, `situacao` e `quantidade`. O
técnico não sai da origem.

## Como usar

```bash
docker compose run --rm carga processar-janelas            # incorpora o que foi registrado desde a última vez
docker compose run --rm carga reprocessar-janela --mes 2024-05
docker compose run --rm carga demonstrar-atraso            # a demonstração abaixo
```

A demonstração insere duas vistorias na origem. Como a origem é insert-only,
elas ficam lá, marcadas na observação. Rode num ambiente de desenvolvimento.

### Testes

Os testes sem banco rodam direto:

```bash
uv run pytest tests/test_janelas.py
```

Os de integração precisam do banco carregado e do `DATABASE_URL` do `.env`,
que aponta para a porta publicada pelo compose:

```bash
docker compose up --build -d                 # sobe o banco e roda a carga
set -a && source .env && set +a              # exporta DATABASE_URL
uv run pytest tests/test_janelas_integracao.py
```

Como a demonstração, eles inserem vistorias de teste na origem.

## Demonstração

Saída real de `demonstrar-atraso` em 12/10/2026, sobre o banco carregado com o
Censo 2024:

```
## 1. Estado de partida

marca d'água: 2026-09-26T16:41:44.889635+00:00 -> 2026-09-26T16:41:44.889635+00:00
vistorias novas desde a marca: 0

## 2. Vistoria da escola 53002580 ocorrida há 3 dias (2026-10-09), registrada agora

marca d'água: 2026-09-26T16:41:44.889635+00:00 -> 2026-10-12T15:46:26.539992+00:00
vistorias novas desde a marca: 1
  janela 2026-10: 17 linhas, impressão 924855e5adcda8679
  vistoria 181070: ocorreu 2026-10-09, registrada 2026-10-12, atraso 3.0 dias -> janela 2026-10 (dentro da tolerância)

## 3. Vistoria ocorrida em 2026-08-15, com a janela 2026-08 fechada desde 2026-09-08, registrada agora

marca d'água: 2026-10-12T15:46:26.539992+00:00 -> 2026-10-12T15:46:27.942649+00:00
vistorias novas desde a marca: 1
  janela 2026-08: 17 linhas, impressão 9388db934a447aebd
  vistoria 181071: ocorreu 2026-08-15, registrada 2026-10-12, atraso 58.7 dias -> janela 2026-08 (FORA da tolerância: janela reaberta)

## 4. Reprocessar a janela 2026-10 duas vezes

1ª: 17 linhas, impressão 924855e5adcda8679
2ª: 17 linhas, impressão 924855e5adcda8679
idênticas: sim
```

O que ela mostra:

- A vistoria de três dias atrás entrou em **outubro**, o mês em que ocorreu,
  dentro da tolerância.
- A vistoria de agosto chegou 58,7 dias depois de ocorrer, com agosto fechado
  desde 08/09. Ela **reabriu agosto**, e a reabertura ficou registrada em
  `_controle.json`.
- A primeira execução não reprocessou nada: a margem trouxe de volta as 181.065
  vistorias da carga, mas todas já estavam na partição.
- Reprocessar outubro duas vezes deu a mesma impressão.

## Medições

| Operação | Tempo | Memória máxima |
| --- | ---: | ---: |
| Demonstração completa (passos 1 a 4) | 3,7 s | 196 MB |
| Reprocessar a janela 2024-05 (carga do Censo, 3.078.105 linhas) | 7,9 s e 8,1 s | 755 MB |

Reprocessar 2024-05 duas vezes deu a impressão `177cbda0abd575cc091396` nas duas,
a mesma da primeira carga. A partição ocupa 2,8 MB em Parquet zstd.

A primeira versão deste código não limitava a memória do DuckDB e foi encerrada
pelo sistema por falta de memória nessa mesma janela. Agora a gravação usa
`memory_limit = 1GB` e transborda para disco quando precisa.

## O que se perde

- **Números de mês fechado podem mudar.** Quem publicou o total de agosto em
  setembro pode ver o número mudar em outubro. A reabertura registrada é o aviso,
  mas o consumidor precisa olhar para ela.
- **Reprocessar custa a janela inteira.** Um único evento atrasado em 2024-05
  regrava 3 milhões de linhas, cerca de 8 segundos. Com a janela mensal e o
  volume atual, é aceitável; a carga anual do Censo concentra quase tudo num mês
  só.
- **A marca d'água vive num arquivo.** `_controle.json` é gravado por troca
  atômica, mas não é transacional com a origem. Se o arquivo se perder, a próxima
  execução trata tudo como carga inicial e regrava todas as janelas: fica certo,
  só mais caro.
- **A margem tem um limite.** Uma transação aberta por mais de 10 minutos antes
  do commit ainda pode escapar da marca. O reprocessamento manual da janela
  corrige.

## Quando revisar

- Se reprocessar uma janela passar de 1 minuto, ou a janela mais pesada passar
  de 1 GB de memória: particionar também por UF dentro do mês.
- Se a fração de eventos fora da tolerância passar de 5% num mês: a tolerância de
  7 dias está curta para o ritmo real de digitação.
- Quando o CDC (issue #25) entregar os eventos em fluxo: a marca d'água em
  arquivo passa a ser a posição (LSN) do slot de replicação.

## Para o ADR 0004

Texto sugerido para a seção de dado atrasado do ADR de ingestão (issue #5):

> Os eventos de vistoria entram na camada analítica em janelas mensais pelo mês
> de `ocorrido_em`, não de `registrado_em`, para que uma vistoria digitada com
> atraso fique no mês em que aconteceu. `registrado_em` serve de marca d'água,
> com uma margem de 10 minutos contra transações confirmadas depois do seu
> `now()`. A janela fica aberta até 7 dias após o fim do mês. O que chega depois
> não é descartado: a janela é recalculada da origem, que é insert-only, e a
> reabertura fica registrada para quem já usou o número. Reprocessar é idempotente:
> regravar a janela de 3.078.105 linhas duas vezes deu a mesma impressão, em cerca
> de 8 segundos. Perdemos a estabilidade dos números de um mês fechado e pagamos
> a janela inteira a cada correção. Demonstração reproduzível com
> `eduinfra demonstrar-atraso`; detalhes em `docs/dado-atrasado.md`.
