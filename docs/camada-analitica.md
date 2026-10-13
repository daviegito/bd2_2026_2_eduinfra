# Camada analítica em formato aberto

A camada analítica ficou guardada como uma tabela Delta na pasta
`dados/warehouse/vistoria_item`. Os dados estão em arquivos Parquet com
compressão zstd, a mesma usada na camada Silver, e são divididos em pastas por
ano do Censo e por UF. O DuckDB consegue ler essa tabela, e a contagem de linhas
é igual à do banco de origem, com 3.078.105 itens do Censo 2024 nos dois lados.

Cada linha da tabela é um item de vistoria de uma escola, e junto com ele já vêm
a UF, o município, a rede (federal, estadual, municipal ou privada) e a
localização da escola. Essas informações estão separadas em três arquivos na
camada Silver. Preferimos juntar tudo uma vez só, na hora de gravar, porque quase
toda consulta analítica filtra por UF ou por rede, e assim ninguém precisa
cruzar as três tabelas a cada consulta. As linhas também são gravadas em ordem
de UF, município, escola e item, o que deixa valores iguais lado a lado e faz a
compressão render mais.

## Por que Delta e não Iceberg

Delta e Iceberg fazem basicamente a mesma coisa. Os dois guardam os dados em
Parquet, garantem que uma gravação entra inteira ou não entra, guardam as
versões anteriores da tabela e deixam acrescentar colunas depois.

A diferença que decidiu a escolha foi o catálogo, que é o lugar onde fica
registrado quais arquivos fazem parte da tabela. No Delta, esse registro fica
numa pasta dentro da própria tabela (`_delta_log`). Então a tabela inteira é só
uma pasta no volume do Docker, e quem baixar o projeto não precisa subir mais
nada. No Iceberg, o catálogo é separado dos dados. Teríamos que manter, por
exemplo, um banco SQLite ao lado da tabela e cuidar para que os dois nunca
ficassem diferentes.

Também pesou o fato de a biblioteca do Delta para Python (`deltalake`) já
conseguir juntar arquivos pequenos e apagar versões antigas sozinha, sem
precisar do Spark. Foi com ela que as medições abaixo foram feitas.

O Iceberg não chegou a ser testado. A comparação foi feita lendo a documentação
das duas bibliotecas. Com essa escolha perdemos algumas vantagens do Iceberg. Ele
é mais adotado por ferramentas de fornecedores diferentes e permite mais tipos
de mudança no esquema, como renomear uma coluna sem regravar os dados. Se um dia
for preciso trocar, regravar a tabela leva poucos segundos com o volume atual,
mas o histórico de versões não vem junto.

## Como rodar

```bash
docker compose run --rm carga publicar-analitica
docker compose run --rm carga comparar-particionamento --repeticoes 15
```

O comando `publicar-analitica` grava a tabela, conta as linhas pelo DuckDB e
compara com o banco de origem. Se as contagens forem diferentes, ele termina com
erro. Esse comando também roda no final do `tudo`. Cada ano é gravado
substituindo apenas as linhas daquele ano, então rodar de novo não duplica nada.

O comando `comparar-particionamento` está em
`src/eduinfra/comparacao_particionamento.py`. Ele grava a mesma tabela de cinco
formas diferentes numa pasta temporária, mede cada uma e apaga a pasta no final.
As cinco formas são estas.

- Tudo de uma vez, sem dividir em pastas.
- Tudo de uma vez, dividido por ano.
- Tudo de uma vez, dividido por ano e UF.
- Em 20 gravações pequenas, dividido por ano e UF. Isso simula uma carga que
  roda várias vezes e grava um pouco de cada vez, que é o que vai acontecer com
  a captura de mudanças.
- As mesmas 20 gravações pequenas, depois de juntar os arquivos, apagar os que
  sobraram e resumir o registro de alterações.

Em cada forma rodamos três consultas. A primeira conta as escolas do DF sem
laboratório de ciências. A segunda conta os itens que faltam nas escolas da
Bahia, separados por rede e localização. A terceira conta os itens que faltam em
cada UF do país. Cada consulta roda uma vez antes de começar a contar o tempo, e
o resultado é a mediana das repetições. As consultas se alternam entre as cinco
formas, como fizemos no documento de
[serialização e compressão](serializacao-compressao.md), para que uma lentidão
momentânea da máquina não prejudique só uma delas. O script também confere se
todas as formas dão as mesmas respostas.

## Resultados

As medições foram feitas dentro do contêiner `carga`, numa
máquina Linux com 16 processadores, usando DuckDB 1.5.5, `deltalake` 1.6.6 e
os dados do Censo 2024, com 15 repetições. Todas as formas deram as mesmas
respostas.

| Forma | Arquivos | Em disco | Tamanho médio | Gravação (s) | DF (ms) | Bahia por rede (ms) | País (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Sem divisão | 1 | 1,6 MB | 1599 kB | 0,54 | 9,7 | 31,5 | 75,7 |
| Por ano | 1 | 1,6 MB | 1590 kB | 0,62 | 10,4 | 31,4 | 73,3 |
| Por ano e UF | 27 | 1,6 MB | 58 kB | 0,17 | 5,6 | 23,4 | 44,3 |
| 20 gravações pequenas | 540 | 3,1 MB | 6 kB | 0,74 | 25,0 | 27,8 | 66,5 |
| 20 gravações, depois de juntar | 27 | 1,7 MB | 62 kB | 0,25* | 12,8 | 34,8 | 55,0 |

\* Nessa linha, o tempo é o de juntar os arquivos, apagar os antigos e resumir o
registro.

Rodamos tudo uma segunda vez e a ordem entre as formas se manteve. Na consulta
do DF, por exemplo, deu 10,9 ms sem divisão e 6,3 ms por ano e UF. Os tempos
mudam um pouco de uma rodada para outra, mas o número de arquivos e os tamanhos
são sempre os mesmos.

### Divisão por ano e UF

Dividir por ano e UF foi a forma mais rápida nas três consultas. Na consulta do
DF isso era esperado, porque o DuckDB só precisa abrir o arquivo do DF e ignora
os outros 26. Na consulta do país inteiro ele lê todos os arquivos, mas consegue
ler vários ao mesmo tempo, um em cada processador. Com um arquivo só isso não
acontece.

Dividir só por ano não muda nada hoje, porque só temos o ano de 2024. Mantivemos
o ano mesmo assim porque é ele que permite substituir um ano inteiro quando o
INEP corrigir o arquivo, sem mexer nos outros anos.

O ponto negativo é que os arquivos ficam pequenos, com 58 kB em média. O
recomendado para Parquet são arquivos de centenas de MB. Isso acontece porque o
Censo inteiro, depois de comprimido, ocupa só 1,6 MB. Mesmo assim, abrir 27
arquivos pequenos por ano ainda custa menos do que o tempo que se ganha
ignorando as UFs que a consulta não usa.

### Juntar arquivos pequenos

O problema que realmente preocupa é cada execução da carga criar arquivos
novos. Com 20 gravações pequenas, a tabela chegou a 540 arquivos de 6 kB e
dobrou de tamanho, indo para 3,1 MB, porque cada arquivo repete o próprio
cabeçalho. Depois de juntar os arquivos, a tabela voltou para 27 arquivos e
1,7 MB em 0,25 segundo, e a consulta do DF caiu de 25,0 para 12,8 ms.

Medindo, percebemos duas coisas que não esperávamos.

A primeira é que, sem resumir o registro de alterações, a tabela ficava mais
lenta depois de juntar os arquivos do que antes. Na consulta do DF deu 34,8 ms
contra 28,2 ms. Isso acontecia porque o DuckDB precisava ler os 22 arquivos do
registro, um para cada gravação, antes de cada consulta. O resumo transforma
esse registro num arquivo só, e por isso passou a fazer parte do processo.

A segunda é que, mesmo depois de juntar, a tabela não fica tão rápida quanto a
gravada de uma vez só. Na consulta do DF foram 12,8 ms contra 5,6 ms. A consulta
da Bahia por rede chegou a ficar mais lenta do que antes de juntar, com 34,8 ms
contra 27,8 ms, e isso se repetiu na segunda rodada, com 35,0 ms contra 28,9 ms.


### Acrescentar uma coluna

Para mostrar que dá para mudar o esquema sem regravar os dados, acrescentamos
na tabela a coluna `lsn_origem`, que guarda a posição de cada alteração no
registro do PostgreSQL.

## Limitações

Os arquivos são pequenos, e cada execução da captura de mudanças pode criar até
27 arquivos novos. Por isso será preciso juntar os arquivos de tempos em tempos.

A UF, o município e a rede de cada escola se repetem nos 17 itens dela. A
compressão compensa isso, mas se uma escola mudar de município, a correção só
aparece depois que o ano dela for gravado de novo.

O DuckDB baixa da internet o complemento que lê tabelas Delta na primeira vez
que é usado. Sem internet nesse momento, a leitura falha.

Cada nova gravação deixa no disco os arquivos da versão anterior até que eles
sejam apagados. Hoje isso dá poucos MB.

Vale rever essa decisão se o tamanho médio dos arquivos passar de 128 MB, se a
pasta de uma UF passar de 1 GB, se a consulta do país ficar mais lenta dividida
por ano e UF do que só por ano, se a tabela passar de 500 arquivos sem que eles
sejam juntados, ou se alguma ferramenta escolhida na E3 ou na E4 só ler Iceberg.

O lugar onde ficam os dados e o registro de alterações está descrito no README,
na seção "Camada analítica (E2)".

## Números para o ADR 0003

- Dividida por ano e UF, a tabela fica com 27 arquivos de 58 kB. Em relação à
  tabela sem divisão, a consulta do DF caiu de 9,7 para 5,6 ms e a do país, de
  75,7 para 44,3 ms.
- Com 20 gravações pequenas, a tabela chegou a 540 arquivos e 3,1 MB. Depois de
  juntar, ficou com 27 arquivos e 1,7 MB, em 0,25 segundo.
- Acrescentar uma coluna não regravou nenhum dos 27 arquivos.
- Para reproduzir, rode `eduinfra comparar-particionamento --repeticoes 15`.
