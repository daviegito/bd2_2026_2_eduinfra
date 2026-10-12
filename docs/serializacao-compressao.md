# Serialização e compressão da camada analítica

A camada Silver fica em **Parquet com compressão zstd**. Entre os formatos
medidos com o Censo 2024 real, ele é o menor em disco: 4,4 MB, contra 6,5 MB do
snappy, 11,9 MB do Parquet sem compressão e 116,4 MB do CSV. Na leitura, os três
Parquets ficam empatados, e o CSV é de 15 a quase 60 vezes mais lento. A medição
responde à issue #28.

## Padrão de acesso analítico

A escolha parte de como a camada é usada, não do padrão da ferramenta.

| Característica | Na camada Silver |
| --- | --- |
| Escrita | uma vez por edição do Censo, em lote; reprocessamento eventual |
| Leitura | pelo pipeline e por consultas analíticas, sempre em lote |
| Forma da leitura | **poucas colunas de muitas linhas**: `vistoria_item` tem 3 colunas e 3.078.105 linhas, e as consultas raramente precisam de todas |
| Filtros típicos | por UF, rede e localização da escola, por item e por situação |
| Busca por chave | não acontece aqui; fica com o PostgreSQL da origem |

Esse é o caso em que um formato colunar ganha: a consulta lê só as colunas de
que precisa, e colunas com poucos valores distintos, como `item_codigo` e
`situacao`, comprimem muito.

## Como reproduzir

Depois de a carga ter gerado a camada Silver:

```bash
docker compose run --rm carga comparar-compressao --repeticoes 15
```

Fora do Docker, com o `.env` carregado:

```bash
uv run eduinfra comparar-compressao --repeticoes 15
```

O comando lê os Parquet da Silver (`municipio`, `escola` e `vistoria_item`),
regrava as três tabelas em cada formato numa pasta temporária, mede e apaga a
pasta. O código está em `src/eduinfra/comparacao_compressao.py`.

**Como se mede:**

- **Escrita:** tempo para gravar as três tabelas, mediana das repetições.
- **Leitura:** três consultas, cada uma com uma execução de aquecimento fora da
  conta, depois a mediana das repetições. As leituras são **intercaladas**: em
  cada repetição, a mesma consulta roda em todos os formatos em sequência. Assim
  uma oscilação da máquina atinge todos os formatos por igual.
- **Conferência:** o comando compara as respostas das consultas em todos os
  formatos. Trocar o formato não pode mudar o dado.

| Consulta | O que exercita |
| --- | --- |
| Poucas colunas | contagem de itens inexistentes por tipo de item: 2 das 3 colunas de `vistoria_item` |
| Filtro por UF | escolas do DF sem laboratório de ciências: junta `vistoria_item`, `escola` e `municipio` e filtra por UF |
| Varredura completa | contagens distintas sobre todas as colunas de `vistoria_item` |

## Resultados

**Ambiente:** 12/10/2026, Linux, 8 CPUs, DuckDB 1.5.5 (a versão do `uv.lock`),
Censo Escolar 2024. Silver com 5.570 municípios, 181.065 escolas e 3.078.105
itens. Mediana de 15 repetições, com cache do sistema operacional quente.

| Formato | Em disco | Relação ao CSV | Escrita (s) | Poucas colunas (ms) | Filtro por UF (ms) | Varredura completa (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CSV (texto) | 116,4 MB | 100% | 0,41 | 219,9 | 329,9 | 353,3 |
| Parquet sem compressão | 11,9 MB | 10% | 0,32 | 10,7 | 8,1 | 23,9 |
| Parquet snappy | 6,5 MB | 6% | 0,30 | 12,4 | 5,7 | 21,9 |
| **Parquet zstd** | **4,4 MB** | **4%** | **0,30** | **11,9** | **5,8** | **18,4** |

Respostas idênticas em todos os formatos: sim.

**Tamanho por tabela, em MB:**

| Tabela | CSV | Parquet sem compressão | Parquet snappy | Parquet zstd |
| --- | ---: | ---: | ---: | ---: |
| `vistoria_item` | 105,60 | 4,42 | 2,43 | 1,74 |
| `escola` | 10,61 | 7,38 | 3,97 | 2,55 |
| `municipio` | 0,15 | 0,12 | 0,08 | 0,06 |

## O que os números dizem

- **Texto contra colunar.** O CSV ocupa 116,4 MB; o mesmo dado em Parquet sem
  compressão ocupa 11,9 MB. Em `vistoria_item` a queda é de 105,6 MB para
  4,4 MB, antes de qualquer codec: o Parquet já codifica por dicionário colunas
  como `item_codigo` e `situacao`, que têm 17 e 2 valores distintos nesta carga.
  Na leitura, o CSV é de 15 a quase 60 vezes mais lento, porque precisa interpretar o texto de
  todas as colunas a cada consulta.
- **Entre os codecs, a diferença é de espaço.** O zstd fica 32% menor que o
  snappy (4,4 contra 6,5 MB) e 63% menor que o Parquet sem compressão. O ganho
  vem quase todo de `escola`, que tem nomes de escola em texto livre: 2,55 MB
  contra 3,97 MB.
- **Na leitura, os três Parquets empatam.** As diferenças entre eles ficam em
  poucos milissegundos e trocam de ordem entre uma rodada e outra. Numa segunda
  rodada idêntica, por exemplo, o Parquet sem compressão foi o mais lento em
  "poucas colunas" (16,9 ms) e o zstd, em "filtro por UF" (8,2 ms). Nenhum codec
  é consistentemente mais rápido.
- **Na escrita, também empatam.** As três tabelas gravam em cerca de 0,3 s com
  qualquer codec, e essa escrita acontece uma vez por edição do Censo.

A medição foi feita com a máquina sob carga de outros programas: carga média
entre 9 e 12 para 8 CPUs. Por isso os tempos absolutos variam entre rodadas, e
as leituras foram intercaladas. Os tamanhos não dependem disso e se repetem
byte a byte.

## Decisão

**Manter Parquet com zstd na camada Silver.** É o menor em disco, sem custo
mensurável de leitura nem de escrita neste volume. O codec agora é uma constante
nomeada, `COMPRESSAO_PARQUET`, em `src/eduinfra/transformacao.py`.

## O que se perde

- **CPU para comprimir e descomprimir.** O zstd faz mais trabalho que o snappy.
  Neste volume a diferença fica dentro do ruído da medição; com várias edições
  do Censo acumuladas, ou com o grão de aluno do ENEM, ela pode aparecer.
- **Compatibilidade de leitura.** O snappy é o codec padrão de quase toda
  ferramenta Parquet e o mais universal. O zstd é lido por DuckDB, Arrow, Spark e
  pelas tabelas Iceberg e Delta, mas um leitor antigo ou muito simples pode não
  suportá-lo.
- **Um arquivo binário.** Diferente do CSV, não dá para abrir e conferir num
  editor de texto. Para inspecionar, é preciso uma ferramenta como o DuckDB.

A decisão é barata de reverter: a Silver inteira é regravada em menos de um
segundo com outro codec, e a origem continua no PostgreSQL e no arquivo bruto.

## Quando revisar

- Se, com mais edições na camada, a varredura completa em zstd ficar mais de 20%
  mais lenta que em snappy numa medição com este mesmo comando.
- Se alguma ferramenta escolhida na E3 ou na E4 não ler Parquet com zstd.

## Para o ADR 0003

Texto sugerido para a seção de serialização do ADR de armazenamento analítico
(issue #4):

> A camada Silver é gravada em Parquet com compressão zstd. Medido sobre o Censo
> 2024 (3.078.105 itens), o zstd ocupa 4,4 MB, contra 6,5 MB do snappy, 11,9 MB
> do Parquet sem compressão e 116,4 MB do CSV. Leitura e escrita ficam
> empatadas entre os três codecs, dentro do ruído da medição. Perdemos um pouco
> de CPU na compressão e a universalidade do snappy, que é o padrão da maioria
> das ferramentas. Reproduzível com `eduinfra comparar-compressao`; detalhes em
> `docs/serializacao-compressao.md`.
