# Comparação da issue #48 — SQL direto e dbt

Data UTC: 2026-11-09T13:56:32.462373+00:00
Python: 3.12.3; sistema: Linux x86_64.
PostgreSQL: PostgreSQL 16.15 (Debian 16.15-1.pgdg12+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 12.2.0-14+deb12u1) 12.2.0, 64-bit.
dbt-core: 1.12.6; dbt-postgres: 1.11.0.

## Condições

Banco de desenvolvimento dedicado, sem slot de CDC. Entradas e origem ficam paradas durante a medição.
A opção nula é o construtor SQL de construir-gold, agendável por cron; mede-se a execução, sem instalar nem medir o agendador.
Recorte público do Censo 2024: SP, DF, GO, MT e MS. ENEM não participa deste benchmark.
Preparo inicial de validação: 11.853 s (fora das amostras). Cada amostra inclui seu próprio preparo.
Entradas: `{"com_enem": false, "linhas": {"censo_escola": 41380, "censo_municipio": 1112, "eventos_vistoria": 703460}, "versao_delta": 1}`.
SQL direto lê cadastros do OLTP e copia a fonte canônica Delta; dbt prepara um retrato Silver/Delta. Cada tempo inclui esse preparo e a igualdade exata confere os resultados.
Cada tempo dbt inclui inicialização do processo. As rodadas são sequenciais, com cache possivelmente aquecido, sem alegação de máquina dedicada.

## Tempo de parede

| Caminho | Rodadas (s) | Mediana (s) |
| --- | --- | ---: |
| SQL direto (com preparo, sem testes declarativos) | 15.255, 14.738, 13.490 | 14.738 |
| dbt run (com preparo) | 28.519, 23.458, 24.406 | 24.406 |
| dbt build (com preparo e testes) | 27.123, 25.760, 25.936 | 25.936 |

## Igualdade exata

EXCEPT ALL nos dois sentidos, em todas as colunas, após cada rodada.

| Tabela | Linhas | Diferenças |
| --- | ---: | ---: |
| dim_item | 17 | 0 |
| dim_municipio | 1112 | 0 |
| dim_escola | 41380 | 0 |
| dim_escola_infraestrutura | 41380 | 0 |
| fato_vistoria_item | 703460 | 0 |
| fato_infra_escola_ano | 41380 | 0 |

## Código, testes e linhagem

| Critério | SQL direto | dbt |
| --- | --- | --- |
| Linhas SQL não vazias/comentários | 166 | 162 |
| Testes declarativos executados por dbt build | não há executor declarativo | 20 |
| Linhagem automática | não há manifest | 11 modelos, 14 dependências diretas e sources em manifest.json |

O contador SQL exclui comentários de linha e linhas vazias; não conta o runner Python, YAML ou testes SQL externos.
A expansão do catálogo dinâmico é contada uma vez com um item nos dois caminhos. A medição não usa linhas de código como medida isolada de manutenção.
A opção nula tem restrições PK/unique no SQL e a suíte pytest existente; não equivale a ausência de validação.

## Como reproduzir

```bash
# DATABASE_URL deve apontar para banco de teste vazio; DIRETORIO_DADOS guarda o download:
uv sync --group transformacao
uv run --group transformacao python transformacao/scripts/comparar.py --confirmar-banco-de-teste --preparar-recorte --repeticoes 3 --saida docs/evidencias/issue-48/comparacao.md
```

Para repetir em banco já preparado, omita --preparar-recorte e aponte DIRETORIO_DADOS para o subdiretório _benchmark48.
O comando recria gold e gold_dbt. Nunca apontar para produção ou para um banco com slot do CDC compartilhado (issue #60).
Os resultados complementam o ADR 0005 integrado na main e não repetem a comparação com SQLMesh.


## Aceite integrado com dados reais — 09/11/2026

Base: `main` em `7f6ef47`, com as #44, #45 e #60 integradas. A validação usou
uma exportação da árvore de trabalho contendo apenas os arquivos necessários ao
build, sem `.env`, `.venv`, dados ou artefatos dbt do host. O projeto Compose
`eduinfra48-aceite9` criou rede, PostgreSQL e volumes novos. As credenciais eram
exclusivas de teste; nenhum banco pessoal/compartilhado foi alterado.

O único comando de construção foi:

```bash
docker compose run --build --rm transformacao
```

O comando foi executado com nome de projeto e arquivo de ambiente de teste
separados. O Compose baixou o Censo oficial, aplicou as migrações até a 0008,
publicou a Silver e o Delta canônico e iniciou o CDC. O runner baixou o ENEM
oficial, executou seu protocolo de manifesto, preparou as sources e construiu
a Gold. Não foram copiados arquivos de dados do host para essa validação.

### Resultados nacionais

| Relação | Linhas |
| --- | ---: |
| `gold.dim_item` | 17 |
| `gold.dim_municipio` | 5.570 |
| `gold.dim_escola` | 181.065 |
| `gold.dim_escola_infraestrutura` | 181.065 |
| `gold.fato_vistoria_item` | 3.078.105 |
| `gold.fato_infra_escola_ano` | 181.065 |
| `gold.fato_desempenho_escola_ano` | 25.179 |
| Entrada ENEM, incluindo escolas sem correspondência | 29.400 |

O ENEM permanece integral na Silver e na entrada: 29.400 escolas e 1.565.797
participantes com escola informada. A Gold contém as 25.179 encontradas no Censo,
com 1.543.488 participantes. As 4.221 excluídas da Gold são as de poucos
participantes/código mascarado, sem correspondência no Censo. As contagens aqui
se referem aos participantes das escolas agregadas, não aos 4.332.944 inscritos
do arquivo inteiro. A cobertura completa do produtor está em [ENEM](../../enem.md).

As **9.266 escolas com 10 a 29 participantes** continuam no fato: não foi aplicado
um corte adicional em 30. As médias e suas contagens foram preservadas, sem
recalcular notas ou converter ausência em zero. A referência analítica foi
`data_referencia_infraestrutura = 2024-11-03`, conforme a configuração aprovada.

SHA-256 dos CSVs registrados no manifesto:

- Censo: `3fb4d93c714b7d9303e34430f0287ca102bf984a4769d5abaca21eb4d1453bc9`.
- ENEM: `6fc3469bb7b67b02840f0d073021ce58358cb68ef896feb017fd5730cb97b49d`.

### Reexecução e grafo

O primeiro build terminou com 13 modelos e os 21 testes então declarados
(`PASS=34`, sem falhas). Após ampliar os testes de contrato do fato ENEM,
as reexecuções terminaram com **13 modelos e 26 testes** (`PASS=39`, sem
WARN/ERROR/SKIP). `dbt docs generate` produziu `index.html`, `manifest.json`
e `catalog.json`; o grafo tem **5 sources e 18 dependências diretas dos modelos**.
A exportação foi servida por HTTP local: `index.html`, `manifest.json` e
`catalog.json` responderam 200, e o catálogo continha os 13 modelos.
Todas as sources dbt apontam para `entrada_transformacao`, sem source de eventos
em `public`. A navegação e o diagrama estão em [Transformação](../../transformacao.md).

O produtor ignorou o ENEM já ingerido. O manifesto permaneceu com uma execução
Censo/2024 e uma ENEM/2024. As contagens e a impressão de todas as colunas das
sete tabelas Gold permaneceram iguais nas reexecuções:

```sql
SELECT count(*), bit_xor(hashtextextended(to_jsonb(t)::text, 0))
FROM gold.fato_vistoria_item t;
```

A mesma consulta foi aplicada a cada mart. Essa impressão de 64 bits é uma
conferência adicional, não uma prova criptográfica de igualdade. A igualdade
exata é verificada pelo `EXCEPT ALL` do benchmark das seis tabelas e pelos
testes de integração, que também conferem o fato ENEM e sua junção temporal.

### Saúde do CDC e frescor

O CDC permaneceu `healthy`, com retenção finita de **2 GB**, consumidor ativo e
checkpoints provocados a cada 10 s durante os builds. A Gold ficou em dia, com
zero vistorias pendentes. O `auditar-cdc` terminou com código 0 e
`Nenhuma violação`, comparando origem e Delta; a fila estava vazia.

| Rodada monitorada | Pico de WAL retido | Estados do slot |
| --- | ---: | --- |
| Dois primeiros builds | 1.041.331.376 bytes (993,09 MiB) | somente `reserved` |
| Build final, após o ajuste de ordem dos snapshots | 1.016.278.512 bytes (969,20 MiB) | somente `reserved` |

A amostragem ocorreu aproximadamente a cada 0,2 s, por uma conexão separada,
sem iniciar um segundo consumidor do mesmo slot. No fechamento do último
build ainda havia WAL aguardando liberação; depois de um checkpoint e novas
passadas do consumidor, ficaram **560 bytes** retidos e o slot `reserved`.
Não houve erro de monitoramento. Esses valores descrevem este ambiente e volume;
não substituem reavaliação para outras cargas, slots ou pausas do consumidor.

### Testes finais e condição de concorrência

```text
15 testes específicos da #48 passaram.
263 passed, 11 warnings in 104.46s
```

A suíte geral foi executada contra banco descartável populado com o recorte
público do benchmark, com Gold disponível. Não houve skips na suíte selecionada.
Foi excluído `tests/test_transformacao_property.py` porque a dependência opcional
Hypothesis não está instalada; nenhuma dependência opcional foi instalada.
As onze advertências são de APIs antigas de particionamento já existentes em
`comparacao_latencia.py`, fora deste escopo.

O teste adicional de concorrência falhou antes do ajuste: um evento confirmado
na origem e no Delta durante a cópia parecia inexistente no snapshot PG mais
antigo. A correção fixa a versão Delta **antes** de iniciar o snapshot PostgreSQL.
O teste confirma sete eventos na versão 0 durante a primeira execução e oito na
versão 1 na seguinte, sem incluir parcialmente a chegada concorrente nem gerar
falso alarme. Também permanecem cobertos duplicatas, rollback das sources,
referência de outro ano, cadastros conflitantes, reexecução, SCD2, linhagem e um
defeito de vigência injetado que o `dbt test` rejeita.

`mkdocs build --strict` e `git diff --check` passaram.

## Checklist e critérios de aceite da #48

- [x] Mesma transformação de infraestrutura em SQL direto e dbt, com comparação
  de código, tempo, testes e linhagem; igualdade exata das seis tabelas.
- [x] Projeto versionado e perfil para o Compose.
- [x] Sources reais da Silver do Censo, ENEM e eventos canônicos.
- [x] Camadas staging/intermediate/marts e convenções declaradas.
- [x] Serviço Compose e procedimento de execução documentados.
- [x] Grafo interativo gerado, com diagrama e navegação documentados.
- [x] Um comando constrói a Gold numa exportação limpa, com banco e volumes novos.
- [x] Comparação com a opção nula salva para complementar o ADR 0005.

O dbt publica modelos em transações separadas, como já discutido no ADR 0005;
a atomicidade das sources não significa atomicidade da Gold inteira. Uma falha
no build exige correção/reexecução antes de tratar a Gold como atualizada.
O benchmark mede infraestrutura; não atribui ao ENEM os tempos desse recorte.
A validação nacional e o benchmark ocorreram na mesma máquina, com outras
atividades locais e caches possivelmente aquecidos; não são um SLA de desempenho.
