# 0005 — Usar o dbt para as transformações da Gold

- **Status:** proposto
- **Data:** 2026-11-03
- **Decisores:** Squad EduInfra

## Contexto

A Gold do PR #55 é SQL puro dentro de `modelo_analitico.py`: seis tabelas (quatro
dimensões, uma delas de variação lenta, e dois fatos), mais uma tabela de staging,
recriadas inteiras por `construir-gold` numa transação `REPEATABLE READ`. Sobre o Censo
2024 completo (181.065 escolas, 3,4 milhões de itens) a construção leva de 60 a 86 s e
gera cerca de 1 GB de WAL (issue #60). A carga é anual, as vistorias novas são estimadas
em 500 por dia, e o painel lê a Gold com SLA de frescor de 25 h (issue #52): uma
construção por dia basta.

A E3 exige transformações versionadas em ferramenta declarativa (dbt ou SQLMesh),
organizadas em camadas, com testes de dados. A Squad definiu o critério: **entre as duas,
a mais simples e fácil de usar**. Restrições: tudo roda localmente pelo Compose, sem
serviço pago; a competência da equipe é SQL e Python; a orquestração da
issue #50 está sendo feita em Dagster; o resultado precisa ser o mesmo da Gold atual.

## Alternativas consideradas

### A. Opção nula — manter o SQL em Python (`construir-gold`) agendado

É a mais rápida, não acrescenta dependência e é a única que constrói todas as tabelas
num retrato único da origem. Não atende ao item da E3 que pede ferramenta declarativa, e
cada teste de dados vira código Python próprio (como no PR #63), sem dependências entre
modelos nem documentação gerada.

### B. dbt (dbt-core com dbt-postgres)

Modelos em SQL com Jinja, testes declarados em YAML, grafo de dependências pelo `ref()`.
Cada modelo roda na sua própria transação.

### C. SQLMesh

Modelos em SQL com um bloco `MODEL`, audits declarados no próprio modelo, plano de
mudanças e backfill por intervalo nativos. Reescreve o SQL pelo sqlglot antes de rodar.

## Medição

A Gold do PR #55 foi montada nas três alternativas, com o **mesmo SQL** (os modelos do
dbt e do SQLMesh foram gerados das constantes de `modelo_analitico.py`), e com os mesmos
12 testes nas duas ferramentas. O dado é um recorte real do Censo 2024: as 41.380
escolas de SP e do Centro-Oeste (23% do total), com 703.460 itens do Censo. O banco da
medição tinha também 29 vistorias de teste nessas escolas, de demonstrações anteriores
(28 com itens, 476 no total): o fato fino medido tem 703.936 linhas. O recorte foi necessário porque a
máquina tinha 1,6 GB livres, e três cópias da Gold completa não cabiam.

Como reproduzir, da raiz do repositório, com o banco no ar e a origem carregada:

```bash
bash docs/evidencias/adr-0005/preparar_amostra.sh       # cria o banco eduinfra_adr0005
bash docs/evidencias/adr-0005/medir.sh                   # 3 reconstruções de cada alternativa
bash docs/evidencias/adr-0005/comparar_resultados.sh gold gold_dbt
bash docs/evidencias/adr-0005/comparar_resultados.sh gold gold_sqlmesh
```

Resultados em 03/11/2026, PostgreSQL 16 em Docker, Linux com 8 CPUs, dbt-core 1.12.6 com
dbt-postgres 1.11.0 e SQLMesh 0.236.4. Os tempos são da rodada 3 de
[resultados.txt](../evidencias/adr-0005/resultados.txt), com as três em sequência.

A comparação foi reproduzida por outro integrante (Davi, no PR #64), num banco só com o
Censo: o fato fino teve 703.460 linhas, e as Golds do dbt e da opção nula saíram iguais.

| Critério | A — opção nula | B — dbt | C — SQLMesh |
| --- | --- | --- | --- |
| Instalação | nenhuma | 60 s, 333 MB, 60 pacotes, sem erro | `sqlmesh[postgres]` **falhou** (compila o `psycopg2`); com `psycopg2-binary` antes: 106 s, 372 MB, 59 pacotes (traz ipython, pandas e numpy) |
| Ajustes até rodar a Gold | — | **0**: rodou de primeira | **5** (lista abaixo) |
| Resultado igual ao da opção nula | — | 6 de 6 tabelas, linha a linha | 6 de 6 tabelas, linha a linha |
| Reconstrução completa, sem testes | 9,2 a 9,3 s | 11,3 a 11,4 s | — |
| Reconstrução com os 12 testes | — | 12,2 a 13,5 s | 59,2 a 65,3 s |
| Testes prontos usados, dos 12 | — | 9 (`unique`, `not_null`, `relationships`, `accepted_values`) e 3 em SQL próprio | 10 (incluindo chave composta e faixa de valores) e 2 de um audit próprio (relacionamento) |
| Colunas do fato anual | lidas do catálogo no banco | lidas do catálogo no banco (`run_query`) | lista fixa no `config.yaml` |
| Retrato único da origem | sim | não: um modelo por transação | não |

Os cinco ajustes do SQLMesh, na ordem em que apareceram:

1. `'@x'` dentro de aspas não foi trocado pelo nome do item. O modelo compilou e geraria
   17 colunas sempre falsas, sem erro.
2. O `->>` foi reescrito para `JSON_EXTRACT_PATH_TEXT`, que não existe para `jsonb` no
   PostgreSQL. Foi preciso escrever `jsonb_extract_path_text` à mão.
3. `@this_model (coluna)` nas pós-instruções foi lido como chamada de função. Foi preciso
   `using btree` no meio e `@IF(@runtime_stage = 'evaluating', ...)` em volta.
4. Na reconstrução, o SQLMesh reaproveita a tabela física e roda as pós-instruções de
   novo: cada `create index` criava uma cópia (o fato fino chegou a 9 índices em vez de
   3). Foi preciso um macro Python que dá nome ao índice.
5. Um audit que consulta outra tabela falhou na primeira construção, porque a tabela
   só é publicada no fim do plano. Foi preciso declarar `depends_on`.

A diferença de tempo vem da forma de reconstruir. O dbt cria uma tabela nova, insere e
só então cria os índices. O SQLMesh faz `DELETE FROM ... WHERE TRUE` e `INSERT` na tabela
que já tem índices: o fato fino sozinho levou 32 s, e cada reconstrução deixa as linhas
antigas mortas até o autovacuum (703.936 no fato fino, contadas depois das reconstruções).

## Decisão

Adotar o **dbt** (dbt-core com dbt-postgres) para as transformações da Gold. Pelo
critério da Squad, foi o único que rodou a Gold de primeira, com o mesmo resultado da
opção nula, sem ajuste e sem erro de instalação. Os testes de dados da E3 (PR #63, issue
#49) passam a ser testes do dbt, e o protótipo em `docs/evidencias/adr-0005/dbt` é o
ponto de partida da issue #48.

## Consequências

**O que ganhamos:** modelos em SQL legível, com dependências declaradas por `ref()`;
`dbt build` constrói e testa na ordem certa; 9 dos 12 testes vieram prontos; integração
oficial com o Dagster (`dagster-dbt`), que é o orquestrador da issue #50; reconstrução
quase no tempo da opção nula (11,3 s contra 9,2 s no recorte).

**O que perdemos:**

- **O retrato único da origem.** A opção nula constrói tudo numa transação
  `REPEATABLE READ`. No dbt, cada modelo vê a origem num instante: uma vistoria gravada
  durante a construção pode entrar no fato fino sem ter versão na dimensão. O
  `left join` deixa a chave nula, e o teste de relacionamento não acusa nulo. Hoje a
  construção roda uma vez por dia e leva segundos, então a janela é curta, mas existe.
- **Testes de chave composta e de faixa** não vêm no dbt-core: aqui são SQL próprio, e o
  teste de distribuição que a E3 pede vai exigir SQL próprio ou o pacote `dbt_utils`.
- **Backfill por intervalo e plano de mudanças**, que são o ponto forte do SQLMesh. Com a
  Gold reconstruída inteira, não fazem falta hoje.
- **Peso na imagem:** 60 pacotes e cerca de 333 MB a mais no contêiner `carga`.
- **Telemetria:** o dbt envia estatísticas anônimas de uso por padrão. É preciso
  desligar (`DO_NOT_TRACK=1` ou `send_anonymous_usage_stats: false`).

**O que se torna irreversível:** nada no dado. O SQL dos modelos é o mesmo da opção nula;
voltar ou trocar para o SQLMesh custa reescrever cabeçalhos, testes e a configuração, que
somam cerca de 90 linhas no protótipo.

## Gatilho de revisão

- Se a reconstrução completa da Gold no Censo inteiro passar de **10 minutos**: aí o
  backfill incremental passa a valer, e o SQLMesh volta a ser candidato.
- Se, depois de uma construção, alguma linha do fato fino ficar com `escola_infra_sk`
  nula com item conhecido (sintoma do retrato perdido): agendar a construção numa janela
  sem escrita ou voltar a construir numa transação só.
- Se um segundo ano do Censo entrar e o tempo da construção passar a crescer com o
  número de anos: medir modelos incrementais no dbt antes de trocar de ferramenta.

## Evidências

- [Protótipos, scripts e saídas brutas](../evidencias/adr-0005/README.md).
- [Modelo analítico](../modelo-analitico.md) e PR #55, que definem a Gold medida.
- [Frescor e SLA da Gold](https://github.com/daviegito/bd2_2026_2_eduinfra/pull/62), de onde vem a frequência diária.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Compara dbt, SQLMesh e a opção nula sobre a Gold e escolhe o dbt | [Gabriel Lopes de Amorim](https://github.com/BrzGab) |  | 3 de novembro de 2026 |
