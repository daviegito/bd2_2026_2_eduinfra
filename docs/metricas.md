# Métricas

As métricas da plataforma são definidas em
[`metricas/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/metricas),
um arquivo por métrica. Qualquer consumidor (painel, consultas, ETL reverso) deve
seguir essa definição.

| Métrica | Descrição | Grão do resultado | Versão |
| --- | --- | --- | --- |
| [`media_enem_escolas`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/media_enem_escolas.yml) | Média das escolas no ENEM, por área | grupo filtrado | 1.2.0 |
| [`diferenca_media_com_sem_item`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/diferenca_media_com_sem_item.yml) | Diferença de nota entre escolas com e sem um item, na mesma rede | rede x item | 1.2.0 |
| [`pct_escolas_por_situacao_do_item`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/pct_escolas_por_situacao_do_item.yml) | Percentual de escolas por situação de um item | grupo filtrado x situação | 1.2.0 |
| [`cobertura_enem_censo`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/cobertura_enem_censo.yml) | Percentual de escolas do ENEM encontradas no Censo | nacional x ano | 1.2.0 |
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

`ano` é inteiro obrigatório entre 1 e 9999; não existe ano implícito.
Um ano sem dados é um recorte vazio válido. `municipio` é código IBGE inteiro
e precisa existir em `gold.dim_municipio`; código desconhecido é erro. UF em maiúsculas; rede: `federal`, `estadual`, `municipal`, `privada`;
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
Com o #110, **eduinfra_dbt publica e é dono dos objetos**. A CLI usa
`DATABASE_URL_TRANSFORMACAO` para publicar (e `DATABASE_URL` para consultar);
na ausência da primeira, usa a DSN configurada. O orquestrador pode publicar
assumindo o papel dbt, do qual é membro. A carga não recebe CREATE ou leitura da
entrada congelada para contornar essa separação.

Funções públicas são `SECURITY DEFINER`, com `search_path` fixo em
`pg_catalog, pg_temp`, SQL versionado e referências qualificadas. Painel e análise
recebem somente `USAGE` no schema, `SELECT` em `metricas.definicoes` e `EXECUTE`
nas funções ativas. Não recebem acesso a `base_analise`, `base_cobertura` ou ao
validador interno; `PUBLIC` não recebe execução. A publicação revoga também os
privilégios padrão amplos que a 0010 cria para tabelas futuras em `metricas`.
Isso restringe esta interface; não revoga o acesso direto à Gold que o #110
concede separadamente a painel/análise. Se a supressão precisar proteger toda a
Gold, a matriz de acesso da #86 também terá de restringir essas tabelas.

Na atualização de uma instalação já publicada, a etapa administrativa de migração
chama `semantica.preparar_acesso`: normaliza donos das funções antigas e fecha
as bases antes da publicação de rotina. No #110, que reorganizou a CLI, essa chamada
precisa ser preservada em `origem/comandos.py:migrar`, após o Alembic, usando a DSN
administrativa. Depois, a publicação dbt recria o contrato com os privilégios
restritos. Não executar novamente a 0010 isoladamente depois dessa etapa.
A bancada aplica o snapshot `0d83bc2` da 0010 e cobre a atualização de objetos
anteriores ao controle de acesso. Não expõe credenciais no resultado.

Após mudar o catálogo ou reconstruir a Gold, execute a publicação novamente.
A interface Python verifica versão e hash composto (YAML, todos os arquivos SQL e versão da interface) contra `metricas.definicoes`; se divergirem,
a leitura falha pedindo republicação. Leitores SQL devem acompanhar essa view para
identificar a versão publicada. `prioridade_vistoria` segue como rascunho, sem função
publicada: depende da confirmação das regras para a #85.

A migração dos cálculos duplicados do painel e dos outros consumidores deve ocorrer
nas respectivas issues. Esta interface não altera a branch de outro integrante.
A [comparação reproduzível](evidencias/issue-83/README.md) e o
[ADR 0006](adr/0006-consumo-etl-reverso.md) fundamentam a escolha.

### Gold sem ENEM

Se o fato de desempenho ou a entrada ENEM não existir, a publicação mantém apenas
`pct_escolas_por_situacao_do_item`; as funções dependentes do ENEM deixam de ser
publicadas e a API informa indisponibilidade, em vez de simular dados ausentes.
O perfil de infraestrutura usa o fechamento do ano (ou o instante atual, se anterior),
a mesma referência temporal anual da Gold de infraestrutura. Ao reconstruir com ENEM,
a republicação volta a expor as quatro métricas. Métricas desativadas/renomeadas têm
as funções da publicação anterior removidas na mesma transação.

### Validação do contrato no CI

O job `Validar contrato semântico e papéis` usa PostgreSQL descartável e a fixture
sintética, separadamente da carga nacional. Executa testes de semântica, publicação,
CLI, catálogo e orquestração; o job falha se houver teste ignorado. O teste conjunto
usa a migração do #110 em commit fixado, que precisa ser atualizado e revalidado
quando o contrato de acesso mudar. Veja a [bancada](evidencias/issue-83/README.md).
