# Consulta otimizada

Esta página mostra como uma consulta foi otimizada a partir do plano de execução, que é o caminho
escolhido pelo PostgreSQL para rodar a consulta e aparece no `EXPLAIN`. O plano mostrou que o custo
está em recalcular o estado atual das escolas a cada leitura, então a otimização foi ler esse
estado já materializado na camada Gold. Os planos e as saídas estão listados em
[Evidências](#evidencias).

## A consulta

A consulta conta quantas escolas não têm biblioteca em cada rede de ensino (federal, estadual,
municipal e privada). Na [caracterização da carga](caracterizacao.md#taxa-e-padrao-de-leitura), ela
aparece como consulta de painel e de pipeline, que precisa responder em até 5 s, e é a mais lenta
das que foram medidas ali, com 1.890 ms. Ela cobre o lado de infraestrutura da pergunta de gestão.

```sql
select e.tp_dependencia, count(*) as escolas
from escola_estado_atual s
join escola e using (co_entidade)
where s.item_codigo = 'biblioteca' and s.situacao = 'inexistente'
group by e.tp_dependencia
order by e.tp_dependencia;
```

`escola_estado_atual` é uma view comum sobre `vistoria` e `vistoria_item`. O `DISTINCT ON` dela
ordena as vistorias da mais recente para a mais antiga e fica com a primeira linha de cada escola e
item. Uma view comum não guarda resultado, então cada leitura refaz esse cálculo.

## O que o plano mostrou

Os dados são o Censo 2024 mais as vistorias de teste, com 3.177.929 itens, 186.937 vistorias e 181.065
escolas. Em [rodada_2/antes.txt](explain/rodada_2/antes.txt), a consulta usa 469.476 blocos (as
páginas de 8 kB que o PostgreSQL lê do cache ou do disco), e o plano aponta três problemas.

1. **Recálculo a cada leitura.** Para achar a vistoria mais recente de cada escola, o plano lê
   186.937 itens em 23.029 blocos de `vistoria_item`, varre `vistoria` inteira e ordena tudo em
   disco (9.160 kB, `external merge`), porque a ordenação não coube na memória.
2. **Estimativa errada.** O planejador, que é a parte do PostgreSQL que escolhe o plano a partir da
   quantidade de linhas que espera encontrar, previa 868 linhas depois do filtro de `situacao`, e
   vieram 110.806, um erro de 127 vezes. Esses 868 são 0,5% das linhas, a seletividade padrão, que é
   a fração fixa suposta pelo planejador quando ele não tem estatística sobre a condição. Aqui não há
   estatística porque o filtro está acima do `DISTINCT ON` da view. Com essa estimativa ele escolheu
   um nested loop em `escola`, que busca em `escola` uma linha para cada linha que vem da view. Isso
   funciona bem com poucas linhas, e com 110.806 buscas custou 443.220 blocos (94% do total).
3. **Disco temporário.** A tabela de hash que junta `vistoria_item` a `vistoria` e a ordenação
   passaram de `work_mem`, a memória que cada uma dessas operações pode usar antes de ir para o
   disco, e escreveram 2.153 blocos temporários.

## Hipóteses testadas na origem

Antes de mudar de camada, testamos o que o plano sugeria sem sair da origem. Cada linha rodou numa
transação desfeita, com o resultado em [hipoteses.txt](explain/hipoteses.txt).

| Hipótese | Blocos em cache | Blocos lidos | Temporários escritos | Linhas no join (estimadas / reais) | Decisão |
| --- | ---: | ---: | ---: | ---: | --- |
| Linha de base | 443.220 | 26.256 | 2.153 | 868 / 110.806 | |
| Estatística estendida em `item_codigo` e `situacao` | 443.219 | 26.257 | 2.153 | 868 / 110.806 | Descartada: a estatística não alcança o que está acima do `DISTINCT ON` |
| `work_mem` de 64 MB | 443.220 | 26.256 | 0 | 868 / 110.806 | Descartada: tira o disco temporário e não muda o que é lido |
| `enable_nestloop` desligado | 90.144 | 26.811 | 2.153 | 868 / 110.806 | Só diagnóstico: confirma o custo da estimativa, mas forçar o planejador não é solução |
| Filtro em `count(*) filter (...)` | 114.995 | 28.496 | 4.306 | 102.096 / 90.532 por processo | Descartada: acerta a estimativa e dobra o disco temporário |
| Índice em `(item_codigo, situacao, vistoria_id)` | 447.210 | 0 | 2.153 | 868 / 110.806 | Descartada: evita ir à tabela, mas mantém o recálculo e o nested loop |
| Filtro e índice juntos | 98.895 | 0 | 4.306 | 102.096 / 90.532 por processo | Descartada: nenhum bloco lido, com o mesmo recálculo |

As medianas de tempo dessas linhas ficaram entre 615 e 1.277 ms, sem ordem clara, porque a máquina
não estava dedicada. Nenhuma hipótese remove o problema 1, e um índice na origem ainda seria pago em
cada vistoria gravada por uma consulta que deve sair dali.

## O que mudou

A caracterização já previa materializar esse estado se a consulta fosse para o caminho interativo,
e a Gold faz isso. `gold.fato_infra_escola_ano` guarda uma linha por escola e ano, com um booleano
por item. A leitura deixa de recalcular e o filtro passa a ser numa coluna com estatística.

```sql
select e.tp_dependencia, count(*) as escolas
from gold.fato_infra_escola_ano f
join gold.dim_escola e using (co_entidade)
where f.biblioteca is false
  and not exists (
      select 1 from gold.fato_infra_escola_ano posterior
      where posterior.co_entidade = f.co_entidade and posterior.ano > f.ano
  )
group by e.tp_dependencia
order by e.tp_dependencia;
```

O `not exists` descarta a linha de uma escola quando existe outra de ano posterior, então sobra a
mais recente de cada escola e a resposta é a mesma da view. O PostgreSQL executa isso como anti-join,
uma junção que mantém só as linhas sem correspondência do outro lado. A primeira versão usava
`DISTINCT ON`, e [o plano](explain/gold_com_distinct_on.txt) mostrou 372.961 blocos para percorrer a
chave primária e visitar a tabela a cada linha, então a troca pelo anti-join leva o total a 7.125.

O painel não precisa disso, porque o grão do projeto é 2024:

```sql
select e.tp_dependencia, count(*) as escolas
from gold.fato_infra_escola_ano f
join gold.dim_escola e using (co_entidade)
where f.ano = 2024 and f.biblioteca is false
group by e.tp_dependencia
order by e.tp_dependencia;
```

Nenhum índice novo entrou. O plano do painel lê o fato inteiro em 2.496 blocos e descarta poucas
linhas, porque 181.065 das 186.066 são de 2024 e o filtro de `biblioteca` mantém perto de 60% delas.
Um índice nessas colunas teria pouco a delimitar.

## Resultado

O comando `comparar-consulta` rodou duas vezes, na mesma cópia dos dados e com as mesmas
configurações, para ver o que se repete de uma rodada para outra. A rodada 2 é uma repetição e não
corrige a 1. Os blocos ficaram iguais, com diferença de até 4, e os tempos mudaram.

Em cada rodada, cada consulta teve duas execuções de aquecimento, descartadas, e depois 5 execuções com
`TIMING OFF`, que tira a cronometragem de cada etapa do plano e mantém o tempo total. As execuções
das três consultas foram alternadas entre si, para a oscilação da máquina não cair toda sobre uma
delas. A tabela traz a mediana, com os buffers da raiz do plano, que já somam as etapas abaixo dela.

| Medida | Rodada 1, view | Rodada 1, Gold | Rodada 1, painel | Rodada 2, view | Rodada 2, Gold | Rodada 2, painel |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tempo de execução | 749,1 ms | 142,8 ms | 73,3 ms | 840,0 ms | 229,4 ms | 102,1 ms |
| Tempo lendo do disco | 61,2 ms | 15,4 ms | 0 ms | 72,3 ms | 23,7 ms | 0 ms |
| Blocos em cache (`shared hit`) | 443.214 | 2.515 | 4.629 | 443.218 | 2.515 | 4.629 |
| Blocos lidos (`shared read`) | 26.262 | 4.610 | 0 | 26.258 | 4.610 | 0 |
| Temporários escritos (`temp written`) | 2.153 | 0 | 0 | 2.153 | 0 | 0 |

O total de blocos cai de cerca de 469 mil para 7 mil na Gold, sem disco temporário. Os blocos lidos
da Gold aparecem porque a view, rodando entre uma execução e outra, tira o fato do `shared_buffers`,
o cache de blocos do PostgreSQL, que tem 128 MB. No plano de diagnóstico, sem essa alternância, são
7.125 em cache e nenhum lido.

O tempo acompanhou. As execuções da view ficaram entre 522,4 e 1.296,1 ms e as da Gold entre 113,4 e
297,4 ms, sem sobreposição. Os planos salvos vão de 947,2 para 220,6 ms na rodada 1 e de 1.167,0 para
123,7 ms na rodada 2. A máquina rodava outros contêineres, então os tempos são indicativos e os
buffers, que se repetem, são a medida mais estável.

A estimativa também deixou de ser um problema. Nos resumos das duas rodadas, os erros de estimativa
de 10 vezes ou mais aparecem só no plano da view.

## Custos

Os valores vêm de [custo_da_gold.txt](explain/custo_da_gold.txt).

| Custo | Valor |
| --- | --- |
| WAL de uma execução de `construir-gold` | 968 MB |
| Duração de `construir-gold` | 333,8 s nessa execução. Duas anteriores, sem arquivo, levaram 112,1 e 144,2 s |
| Espaço da Gold | 908 MB, dos quais 25 MB são o fato largo e 22 MB são `dim_escola` |
| Frescor | A view vê a vistoria assim que ela é confirmada, e a Gold só depois da próxima construção |

O espaço e o WAL são do modelo dimensional inteiro, que existe para mais do que esta consulta.

## Limites

- O WAL de `construir-gold` quase alcança o limite de 1 GB de `max_slot_wal_keep_size`, e o consumidor
  do CDC não avança o slot quando a passada não encontra transação. Por isso a medição rodou numa cópia
  dos dados, num cluster separado sem slot, com a mesma imagem e as mesmas configurações de memória.
- `biblioteca is false` conta também escolas com o item em obra ou inutilizável, e a view conta só
  `inexistente`. Hoje a origem só tem `existente` e `inexistente`, então as respostas são idênticas. Se
  surgirem as outras situações, `comparar-consulta` sai com código 1.
- O painel responde 11.543, 82.416 e 20.518 para as redes estadual, municipal e privada, contra
  10.762, 79.595 e 20.430 da view. A diferença vem das 5.001 linhas de 2026, todas de vistorias de
  teste que marcam o item como existente. A rede federal fica em 19 nas duas.
- Quem precisa do dado no instante da vistoria continua lendo a view, com o plano mostrado aqui.

## Como reproduzir

Os números desta página foram medidos com a Gold do commit `183d9e9`, construída por
`construir-gold`. Para não arriscar o slot do banco principal, crie a cópia na raiz do repositório:

```bash
docker run -d --name eduinfra-medicao-gold -p 127.0.0.1:55999:5432 \
  -e POSTGRES_USER=eduinfra -e POSTGRES_DB=eduinfra -e POSTGRES_HOST_AUTH_METHOD=trust \
  pgvector/pgvector:pg16 postgres -c wal_level=logical
docker compose exec -T banco pg_dump -U eduinfra -d eduinfra --no-owner --no-publications --no-subscriptions \
  | docker exec -i eduinfra-medicao-gold psql -q -U eduinfra -d eduinfra
docker exec eduinfra-medicao-gold psql -U eduinfra -d eduinfra -c "vacuum analyze"
export DATABASE_URL=postgresql://eduinfra@127.0.0.1:55999/eduinfra
```

Desde o [ADR 0008](adr/0008-fonte-canonica-de-eventos.md), `construir-gold` lê a tabela Delta
`warehouse/eventos_vistoria`, que `publicar-analitico` cria a partir da origem. Esse comando também
cria o slot do CDC, que a cópia não usa, então apague o slot logo depois:

```bash
export DIRETORIO_DADOS="$(mktemp -d)"
uv run python -m eduinfra.cli publicar-analitico
docker exec eduinfra-medicao-gold psql -U eduinfra -d eduinfra -c "select pg_drop_replication_slot('eduinfra_cdc')"
```

Depois, construa a Gold e gere as evidências:

```bash
uv run python docs/explain/custo_da_gold.py > docs/explain/custo_da_gold.txt
uv run python -m eduinfra.cli comparar-consulta --repeticoes 5 --pasta-dos-planos docs/explain/rodada_1
uv run python docs/explain/hipoteses.py > docs/explain/hipoteses.txt
```

A segunda rodada é o mesmo `comparar-consulta` com `--pasta-dos-planos docs/explain/rodada_2`.
`comparar-consulta` só lê e sai com código 1 se a view e a Gold devolverem linhas diferentes.
`hipoteses.py` desfaz cada hipótese, mas bloqueia `vistoria_item` enquanto testa o índice e gera WAL,
então não rode esse script no banco principal.

## Evidências

| Arquivo | O que mostra |
| --- | --- |
| [rodada_1/antes.txt](explain/rodada_1/antes.txt), [depois.txt](explain/rodada_1/depois.txt) e [painel.txt](explain/rodada_1/painel.txt) | Os planos com `ANALYZE`, `BUFFERS`, `VERBOSE` e `SETTINGS`, também em JSON, como em [antes.json](explain/rodada_1/antes.json) |
| [rodada_1/resumo.txt](explain/rodada_1/resumo.txt) e [rodada_2/resumo.txt](explain/rodada_2/resumo.txt) | A saída do comando em cada rodada. A `rodada_2/` tem os mesmos arquivos da primeira |
| [rodada_1/serie.json](explain/rodada_1/serie.json) | Todas as execuções da série, com buffers separados |
| [hipoteses.txt](explain/hipoteses.txt) | A tabela e os planos das hipóteses, gerados por [hipoteses.py](explain/hipoteses.py) |
| [custo_da_gold.txt](explain/custo_da_gold.txt) | O custo de construir a Gold, gerado por [custo_da_gold.py](explain/custo_da_gold.py) |
| [gold_com_distinct_on.txt](explain/gold_com_distinct_on.txt) | A consulta e o plano da versão com `DISTINCT ON`, trocada pelo anti-join |

As séries usam `TIMING OFF` e os planos de diagnóstico não, então os tempos dos dois nunca entram
na mesma mediana. Os arquivos trazem o plano de execução, sem valor de linha da origem.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Otimização da agregação nacional a partir do plano de execução, com a leitura materializada na Gold | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 1 de novembro de 2026 |
| 1.1 | Explica os termos do plano e as duas rodadas, simplifica a descrição da consulta e tira as referências a issue e PR | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 7 de novembro de 2026 |
| 1.2 | Inclui na reprodução a publicação da fonte de eventos, que a Gold passou a ler | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 9 de novembro de 2026 |
