# Métricas

As métricas da plataforma são definidas em
[`metricas/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/metricas),
um arquivo por métrica. Qualquer consumidor (painel, consultas, ETL reverso) deve
seguir essa definição.

| Métrica | Descrição | Grão do resultado | Versão |
| --- | --- | --- | --- |
| [`media_enem_escolas`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/media_enem_escolas.yml) | Média das escolas no ENEM, por área | grupo filtrado | 1.1.0 |
| [`diferenca_media_com_sem_item`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/diferenca_media_com_sem_item.yml) | Diferença de nota entre escolas com e sem um item, na mesma rede | rede x item | 1.1.0 |
| [`pct_escolas_por_situacao_do_item`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/pct_escolas_por_situacao_do_item.yml) | Percentual de escolas por situação de um item | grupo filtrado x situação | 1.1.0 |
| [`cobertura_enem_censo`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/cobertura_enem_censo.yml) | Percentual de escolas do ENEM encontradas no Censo | nacional x ano | 1.1.0 |
| [`prioridade_vistoria`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/prioridade_vistoria.yml) | Escolas públicas a vistoriar primeiro | escola | 0.1.0 (rascunho) |

## Regras comuns

- A média do ENEM é a média das médias escolares: cada escola tem o mesmo peso.
- Grupos com menos de 10 escolas com nota não têm média exibida.
- "Sem o item" significa situação `inexistente`.
- Comparações de nota são feitas dentro da mesma rede de ensino.

## Formato do arquivo

| Campo | Conteúdo |
| --- | --- |
| `nome` | igual ao nome do arquivo |
| `titulo`, `descricao` | o que o número significa |
| `versao`, `status` | `ativa`, `rascunho` ou `descontinuada` |
| `historico` | todas as versões, da mais recente para a mais antiga, com data e o que mudou |
| `dono` | responsável por aprovar mudanças |
| `formula` | SQL executável sobre as views, com o CTE `parametros` fornecido pelo publicador |
| `grao` | o que é uma linha nas tabelas lidas |
| `fonte` | tabelas lidas |
| `filtros` | o que pode ser escolhido no painel ou na consulta |
| `supressao` | `minimo` de escolas e a `regra` de quando o valor não é mostrado |
| `consumidores` | quem usa a métrica |
| `interface` | nomes técnicos dos filtros aceitos e se exige item |

## Versionamento

| Mudança | Versão |
| --- | --- |
| O valor muda para a mesma entrada (fórmula, grão, filtro, supressão) | maior: 1.0.0 → 2.0.0 |
| Novo filtro, sem alterar valores existentes | menor: 1.0.0 → 1.1.0 |
| Só texto | correção: 1.0.0 → 1.0.1 |

Toda nova versão entra no topo do campo `historico` do próprio arquivo.

## Cálculos duplicados

- A contagem de escolas sem biblioteca em `otimizacao_consulta.py` usa
  `biblioteca is false`, que inclui `em_obra` e `inutilizavel`; o painel usa só
  `inexistente`.
- A cobertura ENEM x Censo é calculada em `painel.py`, `cobertura_enem.py` e
  `fontes_transformacao.py`.

## Teste

`tests/test_metricas.py` verifica se cada arquivo tem os campos obrigatórios, se a
versão atual é a primeira do `historico`, se as tabelas da Gold citadas existem e se o mínimo de
supressão do painel é o mesmo da definição. Roda com os testes sem banco e na etapa
`testar_gold` do orquestrador.

```bash
uv run pytest tests/test_metricas.py -q
```

## Interface executável da camada semântica

Depois de construir a Gold, publique as definições ativas. O orquestrador já executa
`publicar_metricas` depois do dbt e antes de `testar_gold`. Para execução manual:

```bash
docker compose run --rm carga publicar-metricas
docker compose run --rm carga consultar-metrica --metrica media_enem_escolas --filtros '{"ano":2024,"uf":"DF","area":"media_mt"}'
docker compose run --rm carga consultar-metrica --metrica diferenca_media_com_sem_item --filtros '{"ano":2024,"item":"biblioteca"}'
```

A leitura devolve JSON com `metrica`, `versao`, `filtros` enviados e `dados`.
Python: `eduinfra.semantica.consultar(dsn, nome, filtros)` devolve o mesmo objeto.
No SQL, a função homônima devolve somente o array de dados:

```sql
select metricas.media_enem_escolas('{"ano":2024,"uf":"DF","area":"media_mt"}'::jsonb);
select * from metricas.definicoes;
```

| Métrica | Filtros aceitos |
| --- | --- |
| `media_enem_escolas` | `ano`, `uf`, `municipio`, `rede`, `localizacao`, `area`, `item` e `situacao` juntos |
| `diferenca_media_com_sem_item` | `ano`, `uf`, `municipio`, `rede`, `localizacao`, `area`, `item` obrigatório |
| `pct_escolas_por_situacao_do_item` | `ano`, `uf`, `municipio`, `rede`, `localizacao`, `item` obrigatório |
| `cobertura_enem_censo` | somente `ano` |

`ano` é inteiro obrigatório; não existe ano implícito. `municipio` é código IBGE
inteiro. UF em maiúsculas; rede: `federal`, `estadual`, `municipal`, `privada`;
localização: `urbana`, `rural`. Área padrão `media_geral`, ou `media_cn`, `media_ch`,
`media_lc`, `media_mt`, `media_redacao`. Item deve existir em `gold.dim_item`.
Situação: `existente`, `inexistente`, `em_obra`, `inutilizavel`, `nao_informado`.

Filtros desconhecidos e combinações inválidas falham no servidor, inclusive quando
a população está vazia. Cobertura não aceita UF/rede: códigos ENEM sem correspondência
não podem receber esses atributos com segurança. Filtro opcional ausente significa
sem restrição; JSON `null` nos filtros textuais também significa ausência.

Média devolve `n_escolas`, `media`, `desvio_padrao` amostral. Diferença devolve uma
linha por rede com `n_com`, `n_sem`, `media_com`, `media_sem`, `diferenca`; um lado
abaixo de dez escolas suprime toda a rede. Percentuais devolvem `situacao`,
`n_escolas`, `percentual`, incluindo desconhecidos no denominador; arredondamento
individual pode fazer a soma diferir de 100,0. Cobertura devolve `escolas_enem`,
`encontradas`, `nao_encontradas`, `mascaradas`, `percentual`.

População vazia ou média suprimida devolve `[]`; cobertura vazia devolve contagens
zero e percentual `null`. Notas nulas não entram na contagem de médias. O percentual
de infraestrutura usa todas as escolas Censo do ano e o perfil SCD na data de
referência, incluindo escolas sem ENEM; médias usam o perfil referenciado pelo fato.

A publicação é transacional e não escreve na origem nem materializa outra cópia da
Gold. Precisa de permissão para criar objetos em `metricas` e ler suas fontes.
Funções são `SECURITY INVOKER`: o leitor precisa de `USAGE` no schema `metricas`,
`SELECT` nas views utilizadas e `EXECUTE` nas funções. As views usam as permissões
do proprietário para acessar as tabelas subjacentes, conforme o comportamento padrão
do PostgreSQL; esse proprietário precisa ler Gold e a entrada da transformação.
As concessões e essa fronteira de leitura precisam ser conferidas na integração com
o controle de acesso da #86. Não expõem credenciais no resultado.

Após mudar o catálogo ou reconstruir a Gold, execute a publicação novamente.
A interface Python verifica versão e hash contra `metricas.definicoes`; se divergirem,
a leitura falha pedindo republicação. Leitores SQL devem acompanhar essa view para
identificar a versão publicada. `prioridade_vistoria` segue como rascunho, sem função
publicada: depende da confirmação das regras para a #85.

A migração dos cálculos duplicados do painel e dos outros consumidores deve ocorrer
nas respectivas issues. Esta interface não altera a branch de outro integrante.
A [comparação reproduzível](evidencias/issue-83/README.md) e o
[ADR 0006](adr/0006-consumo-etl-reverso.md) fundamentam a escolha.

## Consumo duplo e garantia de consistência (#84)

O painel (`src/eduinfra/painel.py`) lê a cobertura, o limiar de supressão e as versões
publicadas da camada semântica e exibe as versões ativas no rodapé do HTML exportado.

Como segundo consumidor documentado, analistas podem consultar as mesmas métricas
diretamente no PostgreSQL usando o roteiro em
[`metricas/sql/consumidor_analista.sql`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/sql/consumidor_analista.sql):

```bash
psql "$DATABASE_URL" -f metricas/sql/consumidor_analista.sql
```

A consistência entre o painel e a camada semântica é verificada automaticamente em
`tests/test_consumo_duplo.py`, que também falha se a regra de supressão ou cálculo
for alterada em apenas um consumidor.
