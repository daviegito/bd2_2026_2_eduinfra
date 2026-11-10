# ENEM 2024 e junção com o Censo

O ENEM é a variável dependente da pergunta de gestão. Esta página registra de
onde vem o arquivo, como as escolas do ENEM se ligam às do Censo, quanto dessa
ligação funciona e quais critérios a Silver usa para agregar as notas por escola.

## Fonte e download

| Item | Valor |
| --- | --- |
| Página | [Microdados do ENEM — INEP](https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/microdados/enem) |
| Pacote | `https://download.inep.gov.br/microdados/microdados_enem_2024.zip` |
| Conferido em | 08/11/2026: HTTP 200, 526.073.856 bytes, `Last-Modified` 09/07/2026 |
| Arquivo ingerido | `DADOS/RESULTADOS_2024.csv`: 1.683.449.589 bytes, separador `;`, latin-1 |

O 403 do teste automatizado vinha do portal `www.gov.br`, que recusa clientes
automatizados. O pipeline não passa por essa página: ele baixa direto de
`download.inep.gov.br`. Esse servidor tem o mesmo problema de cadeia TLS do Censo.
O `curl` direto teve a conexão encerrada no handshake, e com o contexto do
[ADR 0002](adr/0002-cadeia-tls-incompleta-do-inep.md) (`tls.contexto_para`) a
resposta foi 200. O download reaproveita a retomada por `Range` do Censo.

A partir de 2024, o pacote separa os microdados em dois arquivos:

- `PARTICIPANTES_2024.csv`: perfil e questionário socioeconômico, **sem** escola;
- `RESULTADOS_2024.csv`: escola de conclusão, presença e notas.

Os dois arquivos não têm chave em comum (`NU_SEQUENCIAL` não é `NU_INSCRICAO`,
nota 1 do dicionário). Só o de resultados entra no pipeline. Por isso, o
questionário socioeconômico do aluno não pode ser ligado à escola; o controle
socioeconômico previsto é o INSE, no grão escola.

## Chave de junção: `CO_ESCOLA` × `CO_ENTIDADE`

Pela nota 2 do dicionário de `RESULTADOS_2024`, o `CO_ESCOLA` vem da base de
matrículas do **Censo Escolar 2024**. O INEP faz o cruzamento pelo CPF e
considera só as etapas de prováveis concluintes do ensino médio (27, 28, 29, 37,
38, 71, 32, 33 e 34). Portanto, o código já é o `CO_ENTIDADE` do Censo do mesmo
ano. A exceção é deliberada: quando a escola tem menos de 10 participantes, o
INEP troca o código real por uma máscara.

No arquivo de 2024, observou-se o seguinte:

- todos os códigos têm 8 dígitos e são numéricos;
- os 4.221 códigos de escolas com menos de 10 participantes começam com `60`,
  que não corresponde a nenhuma UF; nenhum código de escola com 10 ou mais
  participantes começa assim;
- a máscara esconde só a identidade. UF, município, rede e localização da escola
  continuam preenchidos;
- nas 25.179 escolas de código real, os dois primeiros dígitos coincidem com a UF
  do município da escola;
- os atributos da escola (município, UF, rede, localização e situação) são os
  mesmos em todas as linhas de um código (zero divergências);
- todas as escolas informadas estão em atividade (`TP_SIT_FUNC_ESC = 1`).

Do lado do Censo, a junção usa a Silver publicada por `ingerir`. Ela guarda as
escolas em atividade com rede e localização informadas.

## Cobertura medida

Saída de `medir-cobertura-enem` sobre o ENEM 2024 e o Censo 2024 reais, guardada
em [evidencias/cobertura-enem/cobertura_2024.md](evidencias/cobertura-enem/cobertura_2024.md):

| Medida | Valor | % |
| --- | ---: | ---: |
| Participantes no arquivo de resultados | 4.332.944 | 100% |
| Participantes com escola informada | 1.565.797 | 36,1% |
| Escolas distintas no ENEM | 29.400 | 100% |
| Escolas com código mascarado (menos de 10 participantes) | 4.221 | 14,4% |
| Escolas encontradas no Censo, sobre todas as do ENEM | 25.179 | 85,6% |
| Escolas encontradas no Censo, sobre as de código real | 25.179 | 100,0% |
| Participantes com escola cuja escola junta com o Censo | 1.543.488 | 98,6% |
| Participantes de escola com código real cuja escola junta | 1.543.488 | 100,0% |
| Escolas de código real que não juntam, mas existem no CSV do Censo | 0 | 0,0% |

| Participantes por escola | Escolas | Participantes | Encontradas no Censo |
| --- | ---: | ---: | ---: |
| 1 | 439 | 439 | 0 (0,0%) |
| 2 a 4 | 1.218 | 3.729 | 0 (0,0%) |
| 5 a 9 | 2.564 | 18.141 | 0 (0,0%) |
| 10 a 29 | 9.266 | 172.061 | 9.266 (100,0%) |
| 30 a 99 | 11.453 | 640.097 | 11.453 (100,0%) |
| 100 ou mais | 4.460 | 731.330 | 4.460 (100,0%) |

**A taxa de junção é de 100% das escolas com código real e 85,6% de todas as
escolas do ENEM.** Todas as escolas que não juntam são as mascaradas. A perda é
pequena em participantes: 22.309, ou 1,4% dos que têm escola.

**Só 36,1% dos participantes têm escola.** É o esperado, porque o INEP só informa
a escola de prováveis concluintes. Pelo arquivo de participantes, 1.617.602
declararam que concluiriam o ensino médio em 2024 (`TP_ST_CONCLUSAO = 2`). Os
1.565.797 com escola equivalem a 96,8% desse total. A comparação é só entre
totais, porque as linhas dos dois arquivos não se ligam. Os demais inscritos já
tinham concluído o ensino médio (1.846.311), são treineiros (844.259) ou não
concluíram nem estão cursando (24.772).

**Escolas com poucos participantes.** Abaixo de 10 participantes, o código é
mascarado e a escola não entra na análise com a infraestrutura. Entre 10 e 29
participantes, estão 9.266 escolas (36,8% das que juntam), com médias mais
sensíveis a poucos alunos. A Silver guarda o número de participantes e de notas
válidas por área para que a Gold possa ponderar ou aplicar um corte; o corte não
é decidido aqui.

## Silver do ENEM

A partição de cada ano fica em `silver/enem/ano=AAAA/` e contém três arquivos:

| Arquivo | Grão | Conteúdo |
| --- | --- | --- |
| `desempenho_escola.parquet` | uma linha por `co_escola` | atributos da escola, contagens e médias |
| `resumo.parquet` | uma linha por ano | totais do arquivo de origem, inclusive de quem não tem escola |
| `origem.sha256` | — | hash do `RESULTADOS_AAAA.csv` que gerou a partição |

Os nomes não repetem os da Silver do Censo (`escola.parquet`,
`vistoria_item.parquet`), porque os leitores dela procuram esses nomes com
`rglob` dentro de `silver/`.

Colunas de `desempenho_escola.parquet`:

| Coluna | Significado |
| --- | --- |
| `ano`, `co_escola` | ano do ENEM e código da escola, a chave |
| `co_municipio_esc`, `sg_uf_esc`, `tp_dependencia_adm_esc`, `tp_localizacao_esc`, `tp_sit_func_esc` | atributos da escola, como vêm do ENEM |
| `participantes` | inscritos com essa escola, presentes ou não |
| `participantes_com_todas_as_notas` | com nota válida nas quatro provas objetivas e na redação |
| `participantes_sem_nota_valida` | sem nota válida em nenhuma das cinco |
| `notas_validas_{cn,ch,lc,mt,redacao}` e `media_{...}` | quantidade de notas válidas e média por área |
| `redacoes_nota_zero` | redações válidas com nota zero |
| `media_geral` | média das cinco notas, só entre quem tem as cinco |
| `poucos_participantes` | menos de 10 participantes, ou seja, código mascarado pelo INEP |

### Critério para ausentes, faltantes e nota zero

| Situação no arquivo | Tratamento | Casos em 2024 |
| --- | --- | ---: |
| Faltou à prova (`TP_PRESENCA = 0`) | sem nota válida; fora da média, nunca como zero | — |
| Eliminado (`TP_PRESENCA = 2`) | sem nota válida; fora da média | — |
| Presente sem nota (dado faltante) | sem nota válida; contado em `notas_objetivas_faltantes` | 0 |
| Presente com nota zero em prova objetiva | nota válida; entra na média; contado em `notas_objetivas_zero` | 9.798 notas |
| Redação com problema (`TP_STATUS_REDACAO` de 2 a 9) | nota zero oficial; entra na média; contada em `redacoes_nota_zero` | 165.245 |
| Faltou ou foi eliminado no primeiro dia | sem redação (status vazio) | — |

O zero é o resultado oficial atribuído pelo INEP, e por isso entra na média. Quem
preferir excluí-lo consegue recalcular sem voltar ao microdado, porque os zeros
não somam:
`media_redacao * notas_validas_redacao / (notas_validas_redacao - redacoes_nota_zero)`.

No arquivo inteiro, 2.990.093 inscritos têm as cinco notas e 1.150.101 não têm
nenhuma nota válida.

`IN_TREINEIRO` existe apenas no arquivo de participantes, então os treineiros
não podem ser filtrados no de resultados. Como o código da escola só é atribuído
a prováveis concluintes, e os treineiros de 2024 são todos `TP_ST_CONCLUSAO = 3`,
espera-se que eles não tenham escola. Isso não é verificável linha a linha.

## Manifesto e reexecução

A ingestão segue o protocolo do Censo ([ADR 0004](adr/0004-ingestao.md)), com o
manifesto generalizado pela migração `0008`:

- `ingestao_execucao.ano_censo` passa a se chamar `ano`, e a coluna nova `fonte`
  (`censo` ou `enem`) identifica a origem da execução. A versão vigente é a última
  execução de `(fonte, ano)`, então uma execução do Censo nunca faz a do ENEM ser
  ignorada;
- as contagens de inserção no OLTP continuam obrigatórias para o Censo e ficam
  nulas no ENEM, que não grava no OLTP. O ENEM registra em `linhas_publicadas`
  quantas escolas a Silver publicou.

Ordem de uma ingestão: a Silver é montada em `_publicacao/` e a linha do manifesto
é confirmada. Só depois disso a partição `silver/enem/ano=AAAA/` é trocada inteira,
por rename de diretório. Se o arquivo e a Silver publicada coincidem com o
manifesto, a execução é ignorada. Se o manifesto está na versão do arquivo mas a
Silver não, só a Silver é republicada.

Fora do Docker, em WSL2, o hash do CSV levou de 1,3 s a 1,6 s e a montagem da
Silver levou de 6,6 s a 12,3 s.

## Como rodar

```bash
docker compose run --rm carga migrar               # aplica a 0008
docker compose run --rm carga ingerir-enem         # baixa cerca de 526 MB na primeira vez
docker compose run --rm carga ingerir-enem         # de novo: ignorada pelo manifesto
docker compose run --rm carga medir-cobertura-enem # exige também a Silver do Censo (ingerir)
```

O pacote fica em `bruto/enem/` no volume de dados. Na primeira execução,
`RESULTADOS_2024.csv` é extraído ao lado dele. O ENEM não faz parte do comando
`tudo`, para não acrescentar 526 MB de download ao `docker compose up`.

## Limites

- Só o layout de 2024 em diante é aceito; o cabeçalho é validado, e um arquivo
  de outro layout é recusado com mensagem.
- A Gold consome esta Silver pelo [fluxo dbt da #48](transformacao.md):
  `docker compose run --build --rm transformacao` constrói
  `fato_desempenho_escola_ano`, incluindo somente escolas encontradas no Censo e
  registrando as exclusões. A referência temporal da infraestrutura é um parâmetro
  explícito da transformação, não um campo individual do ENEM.
