# 0003 — Usar tabelas Delta sobre Parquet zstd nos destinos analíticos e manter a Silver anual em Parquet puro

- **Status:** proposto, aguardando revisão da Squad
- **Data:** 2026-11-02
- **Decisores:** Squad EduInfra (escolha original na issue #24); medição comparativa por Artur Mendonça Arruda

## Contexto

A pergunta é em que formato guardar o dado analítico depois da Bronze. A carga, com detalhes em [caracterizacao.md](../caracterizacao.md):

| Dimensão | Valor |
| --- | --- |
| Volume | 181.065 escolas e 3.078.105 itens no Censo 2024, 17 por escola, 27 UFs |
| Escrita | carga anual em lote; CDC a cada 2 s; cerca de 500 vistorias por dia (8.500 itens), estimativa da equipe, sem produção real |
| Leitura | agregações de poucas colunas, filtradas por UF, rede e item, pelo DuckDB |
| Latência tolerada | menos de 5 s em consulta de painel |
| Histórico | a origem é insert-only ([ADR 0001](0001-modelagem-insert-only-do-sistema-de-origem.md)), então reaplicar um lote só acrescenta linhas |

**Restrições:** execução local por Docker Compose, sem serviço pago nem cluster; ferramentas de código aberto; a equipe trabalha com Python e SQL e não opera Spark; o dado não pode ficar preso ao motor que o escreveu, exigência da E2.

**Situação em 2 de novembro**, quando a medição foi feita (o que mudou depois da E3 está na [revisão dos gatilhos](#revisao-dos-gatilhos-em-24-de-novembro-de-2026)): a Silver é Parquet puro e recebe uma gravação por ano. As tabelas `warehouse/vistoria_item` e `warehouse/eventos_vistoria` são Delta. Quase nada de negócio lê essas tabelas ainda. A Gold do PR #55 lê a origem no PostgreSQL, e a fonte da transformação depende das issues #45 e #48.

## Alternativas consideradas

### A. Opção nula — Parquet puro em tudo

Medida na melhor forma: grava em pasta temporária, renomeia arquivo por arquivo e reaplica lotes com anti-join, o que basta numa origem insert-only. Dispensa log, extensão e biblioteca nova, e é a mais rápida nas consultas filtradas. É o que a Silver já faz e continua fazendo. Não tem commit atômico entre as 27 pastas nem isolamento de leitura durante a compactação, e todo esse código ficaria por nossa conta.

### B. Delta Lake com `deltalake` — escolhida

Log de transações em `_delta_log`, dentro da pasta da tabela. Oferece commit atômico, isolamento de leitura, `merge`, compactação e evolução de esquema sem Spark. Cobra a leitura do log em cada consulta e a extensão `delta` do DuckDB.

### C. Apache Iceberg com PyIceberg 0.12 e catálogo SQLite

As mesmas garantias atômicas e leitura mais rápida que o Delta nas consultas filtradas. No PyIceberg 0.12, a manutenção só oferece `expire_snapshots`, sem reescrita de arquivos de dados, e o `upsert` não escala. Exige catálogo, e o metadado guarda caminhos absolutos.

## Medição

Censo 2024 real, com as respostas conferidas entre as variantes. Ambiente: Linux no WSL2, 8 CPUs, 3,8 GB de memória, disco local, DuckDB 1.5.5, `deltalake` 1.6.6, PyIceberg 0.12.0, medianas de 15 repetições intercaladas, em 2 de novembro de 2026. Os tempos oscilam entre rodadas; contagens e tamanhos se repetem.

Como reproduzir, em Linux e com a Silver gerada:

```bash
uv run --with 'pyiceberg[sql-sqlite,pyarrow]==0.12.0' python -m eduinfra.comparacao_formato_tabela dados/silver --repeticoes 15
uv run --with 'pyiceberg[sql-sqlite,pyarrow]==0.12.0' python -m eduinfra.comparacao_falha_tabela dados/silver
uv run python -m eduinfra.comparacao_leitor_delta dados/silver --repeticoes 15
```

| Métrica | A. Parquet puro | B. Delta | C. Iceberg |
| --- | ---: | ---: | ---: |
| Dados em disco, carga inteira | 1,83 MB | 1,57 MB | 1,62 MB |
| 20 gravações pequenas | 2,17 s | 0,88 s | 3,05 s |
| Compactar de 540 para 27 arquivos | 0,64 s | 0,72 s | 3,03 s |
| Uma UF, depois de compactar | 3,0 ms | 13,6 ms | 4,2 ms |
| Consulta nacional, depois de compactar | 50,7 ms | 68,5 ms | 69,8 ms |
| Reaplicar 8.500 itens | 0,08 s (anti-join) | 0,44 s (`merge`) | 0,28 s (anti-join) |
| Método nativo de reaplicação | nenhum | `merge`: 0,44 s | `upsert`: 49,5 s com 1.700 itens; 8.500 não terminou em 600 s |
| Gravador morto 12 vezes: ilegível ou parcial | 0 | 0 | 0 |
| Gravador morto 12 vezes: reaplicar recuperou | 12 | 12 | 12 |
| Leituras ruins durante a compactação | 17 de 582 (9 contagens erradas, 8 erros) | 0 de 104 | 0 de 507 |
| Copiar a pasta e ler na cópia | lê | lê | falha; só lê com `allow_moved_paths` e uma opção insegura do DuckDB |

Sem a pasta temporária, gravando direto com o `COPY` do DuckDB, o Parquet puro ficou ilegível em 7 das 12 mortes e a reaplicação recuperou só 5. Esse é o custo do caminho mais simples.

**O custo de ler o log**, com os mesmos arquivos lidos pelos dois caminhos:

| Arquivos | Consulta | `read_parquet` | `delta_scan` |
| ---: | --- | ---: | ---: |
| 27 | uma UF | 2,4 ms | 7,5 ms |
| 27 | nacional | 69,2 ms | 65,4 ms |
| 540 | uma escola | 37,8 ms | 23,9 ms |
| 5.400 | uma UF | 94,1 ms | 49,6 ms |
| 5.400 | uma escola | 423,2 ms | 47,1 ms |

Com poucos arquivos, o log custa cerca de 5 ms. A partir de centenas de arquivos, as estatísticas dele evitam abrir o resto. A direção confere com o [artigo do Delta](https://vldb.org/pvldb/vol13/p3411-armbrust.pdf), de autores da Databricks, e com o [post do DuckDB](https://duckdb.org/2025/03/21/maximizing-your-delta-scan-performance.html), ambos em escala bem maior. Citamos as fontes como contexto; a decisão se apoia nos números acima.

**Codec e partição**, com `comparar-compressao` e `comparar-particionamento`: zstd ocupa 4,4 MB, contra 6,5 MB do snappy e 132,7 MB do CSV, com leitura empatada entre os codecs. Particionar por ano e UF deu 7,7 ms contra 11,5 ms sem partição em uma UF, e 65,2 ms contra 87,6 ms na consulta nacional. Acrescentar uma coluna não regravou nenhum dos 27 arquivos.

**Limites da medição:** disco local, sem armazenamento de objetos; o Iceberg foi medido só com o PyIceberg, e outros motores têm compactação própria; 12 mortes não acertaram o intervalo entre os renames do Parquet puro, então a falta de atomicidade entre pastas dele vem da construção, sem ter sido observada.

## Decisão

Manter a Silver em Parquet puro com zstd, porque recebe uma gravação por ano, publicada por troca de pasta. Usar Delta sobre Parquet zstd nas tabelas de `warehouse/`, lidas pelo DuckDB, particionando por ano e UF a tabela de itens e por mês de `ocorrido_em` a de eventos.

O critério contra A é a leitura durante a manutenção. Com CDC a cada 2 s, a tabela precisa de compactação recorrente, e no Parquet puro 17 de 582 leituras simultâneas saíram erradas ou com erro, contra nenhuma no Delta. O ganho de A em velocidade não decide, porque todas as consultas ficam muito abaixo dos 5 s tolerados. O critério contra C é operacional: compactação quatro vezes mais lenta e sem API, `upsert` inviável neste volume, catálogo a manter e cópia da pasta que quebra por padrão.

## Consequências

**O que ganhamos:** leitura isolada durante compactação e `merge`; `merge` e compactação prontos, sem código próprio de troca de pastas; reentrega do CDC sem duplicar; coluna nova sem regravar dado; tabela que cabe numa pasta do volume, sem serviço adicional.

**O que perdemos:**

- *Latência em toda consulta filtrada.* Cerca de 5 ms a mais hoje, e 13,6 ms contra 3,0 ms em uma UF. Fixar o snapshot com `attach` reduz para 4,2 ms, mas aí a leitura não vê gravações novas.
- *Reaplicação mais lenta.* O `merge` levou 0,44 s contra 0,08 s do anti-join, e quem paga é cada passada do consumidor com dado novo.
- *Compactação sem dono.* Ela só existe no script de medição. Sem agendamento, 20 gravações geram 540 arquivos e 3,1 MB, e a consulta de uma UF sobe de 7,7 ms para 26,3 ms. Cabe à orquestração da #50.
- *Tabelas sem consumidor de negócio.* Hoje mantemos Delta e CDC sem que a Gold os leia. Enquanto a #45 não decidir a fonte, esse custo não tem retorno no painel.
- *Rede na primeira leitura.* O DuckDB baixa a extensão `delta`; sem internet nesse momento, a leitura falha.
- *Dois esquemas de partição*, por ano e UF nos itens e por mês nos eventos, sem unificação ([ADR 0004](0004-ingestao.md)).
- *O que o Iceberg daria:* ecossistema mais amplo, renomear coluna sem regravar e leitura filtrada mais rápida (4,2 ms contra 13,6 ms).

**O que se torna irreversível:** quase nada. Regravar a tabela em outro formato levou de 0,7 s a 1,2 s neste volume. O histórico de versões do log não migra, e trocar de formato exige reescrever `eventos.py` e `fluxo_cdc.py`, além da auditoria do CDC se o PR #59 entrar.

## Gatilho de revisão

- A #45 e a #48 fecharem sem que a transformação leia essas tabelas: a decisão fica sem retorno e A volta a ser candidata.
- Consulta de painel acima de **5 s**.
- Tabela acima de **500 arquivos** sem compactação agendada, média acima de **128 MB** por arquivo ou pasta de uma UF acima de **1 GB**.
- Ferramenta da E3 ou da E4 que só leia Iceberg.
- PyIceberg com reescrita de arquivos e `upsert` de 8.500 itens abaixo de **1 s**.
- Saída do disco local para armazenamento de objetos, porque os números acima deixam de valer.

## Revisão dos gatilhos em 24 de novembro de 2026

Depois da E3, conferimos cada gatilho contra o código da main. A Gold passou a ser construída pelo dbt no PostgreSQL ([ADR 0005](0005-ferramenta-de-transformacao.md)), e a `eventos_vistoria` virou a fonte única de eventos ([ADR 0008](0008-fonte-canonica-de-eventos.md)).

| Gatilho | Situação em 24/11 |
| --- | --- |
| #45 e #48 fecharem sem a transformação ler essas tabelas | Disparou para a `warehouse/vistoria_item`. As duas issues fecharam, e o `fontes_transformacao.py` copia para a Gold a Silver em Parquet e a `eventos_vistoria` em Delta. A `vistoria_item` continua gravada pelo `publicar-analitica` em toda execução do `tudo`, e só a conferência de contagem desse comando lê a tabela. |
| Consulta de painel acima de 5 s | Deixou de medir esta decisão, porque o painel lê a Gold no PostgreSQL e não o lake. |
| Mais de 500 arquivos sem compactação agendada, 128 MB por arquivo ou 1 GB por UF | Não medido nesta revisão. A compactação continua sem agendamento, porque nem o orquestrador da #50 nem o consumidor do CDC a chamam. A `eventos_vistoria` recebe um commit a cada passada do CDC com transação nova e é a tabela com mais chance de passar dos 500 arquivos. |
| Ferramenta da E3 ou da E4 que só leia Iceberg | Não disparou na E3. O dbt lê o PostgreSQL, e a cópia para a Gold usa `deltalake` e DuckDB. |
| PyIceberg com reescrita de arquivos e `upsert` de 8.500 itens abaixo de 1 s | Não conferido nesta revisão. |
| Saída do disco local para armazenamento de objetos | Não disparou. O lake continua no volume local do Docker Compose. |

Com o primeiro gatilho disparado, a `vistoria_item` cobra a gravação Delta em toda carga sem dar retorno na Gold. A Squad tem duas saídas, tirar a tabela do `tudo` e deixar a Silver como única cópia dos itens, ou dar a ela um consumidor na E4. A decisão para a `eventos_vistoria` continua de pé, e o custo que falta pagar é agendar a compactação dela.

## Evidências

- `src/eduinfra/formatos_de_tabela.py`, `comparacao_formato_tabela.py`, `comparacao_falha_tabela.py` e `comparacao_leitor_delta.py`, com testes em `tests/test_formatos_de_tabela.py`.
- [Camada analítica](../camada-analitica.md) e [serialização e compressão](../serializacao-compressao.md). O CSV de 116,4 MB citado nesta última é anterior à coluna `ano_censo` na Silver.
- [CDC e semântica de entrega](../cdc-e-semantica-de-entrega.md), [modelo analítico](../modelo-analitico.md) e `src/eduinfra/fontes_transformacao.py`, que copia a Silver e a `eventos_vistoria` para a Gold.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Registra a escolha de Parquet zstd com tabelas Delta Lake para a camada analítica | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 18 de outubro de 2026 |
| 1.1 | Refaz a decisão com A, B e C medidos, falha e leitor concorrente; limita o Delta aos destinos analíticos | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 2 de novembro de 2026 |
| 1.2 | Revisa os gatilhos depois da E3 e atualiza as referências da Gold | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 24 de novembro de 2026 |
