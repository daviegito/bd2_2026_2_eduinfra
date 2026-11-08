# 0008 — Adotar a tabela Delta `eventos_vistoria` como fonte única de eventos, completada pelo CDC e pelas janelas

- **Status:** proposto, aguardando revisão da squad
- **Data:** 2026-11-08
- **Decisores:** Squad EduInfra

## Contexto

Até a E2, dois caminhos levavam as vistorias à camada analítica, e nenhum deles
alimentava a Gold, que lia direto o OLTP ([ADR 0004](0004-ingestao.md)):

- as **janelas mensais** (`processar-janelas`), em Parquet em
  `analitico/eventos_vistoria/ocorrido_mes=AAAA-MM/`, regravadas a partir da
  origem e descobertas por marca d'água sobre `registrado_em`;
- o **CDC** (serviço `cdc`), em Delta em `warehouse/eventos_vistoria`,
  particionado pelo mês de `ocorrido_em`, com merge pela chave
  `(vistoria_id, item_codigo)` e LSN confirmado depois da gravação.

A E3 exige uma transformação sobre uma fonte declarada. Com dois destinos sem
integração, a contagem de um podia divergir da do outro, e a Gold não lia
nenhum deles. A pendência foi registrada no diário da Semana 10.

**Carga.** A origem tem 3.078.139 itens de vistoria: a carga do Censo concentra
3.078.105 deles num mês só (2024-05). A taxa estimada é de 500 vistorias por dia
(8.500 itens), ainda não medida em produção. O grão do evento é o item de
vistoria, com a chave primária de `vistoria_item`.

**Restrições.** Execução local pelo Compose, sem serviço pago. A Gold é SQL no
PostgreSQL e deve continuar portável para dbt ou SQLMesh (ADR 0005, a escrever).
A pergunta de gestão depende da data de ocorrência, então um evento atrasado tem
de cair no mês em que ocorreu.

## Alternativas consideradas

### A. Opção nula — manter os dois destinos, com a Gold lendo o OLTP

É viável: a Gold sempre lê o que a origem tem, e cada caminho já é idempotente.
O problema é que nenhum dos destinos analíticos é usado pela transformação. A
camada analítica vira um subproduto, e a consistência entre os dois destinos
não é conferida por ninguém. Não atende ao requisito da E3.

### B. Janelas em Parquet como fonte canônica

As janelas já têm o mês de `ocorrido_em`, a tolerância e o registro de
reabertura. Mas a descoberta por marca d'água tem um furo declarado: uma
transação aberta por mais de dez minutos escapa da margem. Elas também não têm
chave nem versões, e cada correção regrava o mês inteiro. Tornar o CDC um
alimentador delas exigiria reescrever partições Parquet a cada passada de 2 s.

### C. Delta `eventos_vistoria` como fonte canônica, com as janelas a completá-la — escolhida

A tabela já tem a chave, a partição pelo mês de `ocorrido_em`, o merge que só
insere o que falta e o log de versões. O CDC captura na ordem de commit, sem
depender de `registrado_em`. As janelas deixam de ter destino próprio. Elas
passam a reconciliar a tabela canônica com a origem, mês a mês, pelo mesmo
merge: preenchem o que o CDC não entregou (slot recriado depois de invalidado,
vistoria anterior à criação do slot) e continuam registrando reaberturas.

### D. União das duas tabelas numa visão, com deduplicação na leitura

Daria uma visão única sem mexer nos escritores. Mas continuaria havendo dois
destinos a operar, e cada leitura pagaria a deduplicação sobre os dois. Não
haveria resposta para a pergunta "qual dos dois está certo?" quando divergissem.

## Medição

Banco de desenvolvimento do Compose com o Censo 2024 (3.078.105 itens), em
Docker sobre WSL2, em 08/11/2026:

| Operação | Resultado |
| --- | --- |
| Linha de base na Delta (`publicar-analitico`) | 3.078.105 eventos em 10,5 s |
| Primeira reconciliação das janelas (`processar-janelas`) | 18,6 s; mês 2024-05 conferido por impressão, 0 linhas gravadas |
| Segunda reconciliação, sem nada novo | 5,4 s, quase tudo início do contêiner |
| `demonstrar-atraso` e depois uma passada do `cdc` | 34 itens gravados pelas janelas; o CDC leu os mesmos 34 e inseriu 0 |
| `construir-gold` lendo a fonte | 85,9 s e 66,4 s; carga e conferência da fonte em cerca de 22 s e 16,8 s |
| Conferência na construção | 3.078.139 na fonte, 3.078.139 na origem, 0 só de um lado |

As três construções medidas antes desta mudança, lendo o OLTP, levaram 60,3 s,
85,8 s e 69,3 s ([frescor](../frescor-e-sla.md)).

Como reproduzir, em ambiente de desenvolvimento com a origem carregada:

```bash
docker compose run --rm carga publicar-analitico
docker compose run --rm carga processar-janelas
docker compose run --rm carga demonstrar-atraso   # insere duas vistorias de teste
docker compose run --rm carga cdc
docker compose run --rm carga construir-gold
```

## Decisão

Adotar C:

- **Fonte canônica:** `warehouse/eventos_vistoria`, em Delta. Grão: um item de
  vistoria. Chave de deduplicação: `(vistoria_id, item_codigo)`, a chave primária
  de `vistoria_item`. Partição: `mes_ocorrencia`, o mês de `ocorrido_em` em UTC,
  calculado do mesmo jeito pelos dois caminhos.
- **Escritores:** a linha de base (`publicar-analitico`), o CDC e as janelas
  (`processar-janelas`, `reprocessar-janela`). Todos usam o mesmo merge, que só
  insere a chave que falta. O primeiro caminho a entregar fica registrado:
  `lsn_origem` preenchido indica que veio do CDC. Dois merges no mesmo mês ao
  mesmo tempo entram em conflito no commit; o perdedor tenta de novo, até cinco
  vezes.
- **Janelas:** a reconciliação de um mês compara a impressão do mês na origem
  com a da fonte e só faz merge se forem diferentes. A impressão é a soma exata,
  em 64 bits, do md5 de cada linha, sem `lsn_origem`. O controle (marca d'água,
  janelas e reaberturas) fica em `warehouse/eventos_vistoria_janelas.json`. O
  Parquet `analitico/eventos_vistoria` deixa de ser escrito.
- **Transformação:** `construir-gold` copia uma versão da tabela para
  `fonte.eventos_vistoria` no PostgreSQL, na mesma transação da Gold, com chave
  primária. Em seguida, confere a cópia com `public.vistoria_item`. Os três
  pontos de leitura de eventos passam a ler só essa cópia: `stg_mudanca_item`, o
  fato fino e os anos do fato largo. A construção recusa a fonte que tem evento
  ausente da origem. O que falta na fonte fica pendente no frescor. A versão
  usada fica no comentário da tabela.

## Consequências

**O que ganhamos:** uma fonte declarada, com a mesma chave em todos os
caminhos. A contagem é conferida com o OLTP a cada construção. O evento atrasado
cai no mês de `ocorrido_em` por construção da partição, venha de onde vier. O
furo da margem da marca d'água deixa de ser um risco de perda, porque o CDC
captura pela ordem de commit e as janelas passam a ser a rede de segurança. A
Gold sabe de qual versão da fonte foi construída.

**O que perdemos:**

- A Gold deixa de enxergar o que ainda não chegou à fonte. Se o serviço `cdc`
  parar e ninguém rodar `processar-janelas`, a Gold fica para trás. O frescor
  ([issue #52](../frescor-e-sla.md)) passa a medir o trecho origem → fonte → Gold
  inteiro, e é ele que acusa o atraso.
- Cada construção paga a cópia da fonte: de 17 s a 22 s para 3 milhões de
  eventos, mais um CSV intermediário em disco temporário, com o tamanho da tabela
  sem compressão.
- Vistoria sem item não é evento e não entra na Gold. Antes, ela criava a linha
  do ano no fato anual. O frescor deixou de contá-la como pendente, o que reverte
  a regra do PR #62: com a nova fonte, contá-la deixaria o SLA violado para
  sempre.
- Reconciliar um mês ainda lê o mês inteiro da origem: 3 milhões de linhas em
  2024-05, cerca de 3,5 s de leitura e impressão.
- O controle das janelas continua num arquivo JSON, não transacional com a
  tabela. Se ele se perder, a próxima execução reconcilia todos os meses. O
  resultado fica certo, só mais caro.

**O que se torna irreversível:** nada no dado. A origem é insert-only, e a
tabela Delta pode ser reconstruída dela com `publicar-analitico --forcar`. O
Parquet antigo de `analitico/eventos_vistoria` pode ser apagado; ele pode ser
recriado a partir da origem, mas nenhum código o lê mais.

## Gatilho de revisão

- Se a cópia da fonte passar de 50% do tempo da construção da Gold: ler a Delta
  direto por DuckDB, ou construir incrementalmente.
- Se reconciliar um mês passar de 1 minuto ou 1 GB de memória: particionar
  também por UF dentro do mês.
- Se mais de 1% dos eventos de um mês chegarem pelas janelas, e não pelo CDC: o
  CDC está perdendo eventos e a causa tem de ser investigada.
- Se surgir `UPDATE` ou `DELETE` legítimo na origem: o merge só insere, e a chave
  deixa de bastar.

## Evidências

- [Fonte canônica e janelas](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/src/eduinfra/eventos.py),
  [reconciliação das janelas](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/src/eduinfra/janelas.py) e
  [Gold](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/src/eduinfra/modelo_analitico.py).
- Testes: `tests/test_fonte_de_eventos.py`, `tests/test_fonte_de_eventos_integracao.py`
  (o mesmo evento pelos dois caminhos, nas duas ordens) e
  `tests/test_modelo_analitico.py` (a Gold lê só a fonte e confere com a origem).
- [Dados atrasados](../dado-atrasado.md), com a demonstração sobre a fonte canônica.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Fonte canônica de eventos e unificação dos caminhos de dado atrasado | Squad EduInfra |  | 8 de novembro de 2026 |
