# 0006 — Centralizar métricas em views PostgreSQL e manter consumidores separados

- **Status:** proposto para revisão da Squad
- **Data:** 2026-11-24
- **Decisores:** Squad EduInfra
- **Issues:** #7 e #83; catálogo #82; consumidores #84/#85/#94; acesso #86

## Contexto, consumidores e atores

O gestor escolar usa o painel para identificar infraestrutura ausente e comparar
escolas da mesma rede. A coordenação consulta cobertura e qualidade dos dados para
interpretar essas comparações. O ENEM mede desempenho de escolas participantes,
com supressão abaixo de dez escolas com nota: associação não demonstra causalidade.
Percentuais de infraestrutura usam a população do Censo, incluindo falta de informação.

O operador de vistorias é quem pode agir: recebe uma lista operacional de escolas
priorizadas, registra a ação e produz uma nova vistoria no fluxo transacional.
O ETL reverso transporta uma decisão da Gold para esse fluxo operacional; não deve
reescrever fatos históricos, notas ou a origem durante uma consulta do painel.
O job da #85 deve controlar destino, chave idempotente, versão da regra e recibo de
execução. Esta decisão define o contrato; não implementa essa escrita.

O painel estático Plotly/HTML já é publicado no GitHub Pages e usa filtros locais.
Existem cálculos duplicados em Python, JavaScript e SQL. A branch de evolução do
painel (#94) pertence a outro integrante; esta entrega não altera aquela branch.

## Restrições e critérios

- Execução local pelo Compose, PostgreSQL, dbt e Dagster existentes; sem serviço pago.
- Uma definição versionada por métrica, com dono, filtros, população e supressão.
- Interface por nome e filtros; rejeitar filtros que não podem ser aplicados.
- Atualizar depois do dbt; não acrescentar reconstrução da Gold ou carga de WAL.
- Preservar separação de leitura e escrita e as permissões mínimas da #86.
- Medir definições, atualização, consulta, dependências e propagação de mudanças;
  decidir pela manutenção e adequação ao uso, sem alegar superioridade universal.

## Alternativas

| Candidato | Ganho | Custo e perda |
| --- | --- | --- |
| Nula: manter fórmulas em cada consumidor | nenhuma mudança imediata | painel, consultas e ETL podem usar cortes e denominadores diferentes |
| A: dbt Semantic Layer/MetricFlow local com dbt-postgres | DSL semântica, medidas combináveis, compilação e validação pelo dbt | modelo adicional, CLI e adaptação dos resultados ao contrato do painel; uso incorreto ainda pode duplicar população |
| B: views PostgreSQL e funções de leitura publicadas do catálogo | SQL conhecido, filtros parametrizados, sem serviço ou pacote novo na imagem | validação e interface mantidas pela equipe; sem catálogo semântico gerenciado ou consulta arbitrária por API |
| Plataforma BI/API hospedada | exploração interativa e autorização gerenciada, conforme produto | operação, autenticação e possível custo além do Compose; não foi prototipada nem medida nesta decisão |

O MetricFlow local foi instalado e executado com PostgreSQL; não foi descartado
por incompatibilidade. A [documentação oficial](https://docs.getdbt.com/docs/build/about-metricflow)
e o [projeto oficial](https://github.com/dbt-labs/metricflow) descrevem a ferramenta.
A execução local do CLI usada aqui não exige contratar uma API hospedada.

## Protótipos e medição

[Bancada reproduzível](../evidencias/issue-83/README.md) e
[amostras completas](../evidencias/issue-83/resultados.json).
**Medição original de 24/11, anterior aos ajustes de permissões/hash de 27/11.**
Os tempos e contagens de código abaixo não são novas medições da implementação revisada.

PostgreSQL 16.14; 6.000 escolas **sintéticas**, 5.000 correspondências ENEM e 100
códigos sem correspondência. Cinco repetições após aquecimento. A e B compartilham
as views de junção sobre a Gold: compara-se a agregação, não a construção da Gold.

| Consulta, mediana em ms | A: SQL compilado MetricFlow | B: função sobre views |
| --- | ---: | ---: |
| Média, contagem e desvio-padrão | 139,75 | 37,15 |
| Diferença com/sem biblioteca por rede | 33,72 | 35,63 |
| Percentuais por situação da biblioteca | 114,16 | 13,93 |
| Cobertura ENEM × Censo | 5,84 | 6,37 |
| Média de matemática, DF | 144,60 | 33,67 |
| Média de população vazia, AC | 140,51 | 29,80 |

O CLI de A levou separadamente aproximadamente 3,260–3,617 s para compilar cada consulta.
Esses tempos **não** estão somados à coluna SQL. A atualização teve medianas de
3637 ms (`dbt run` completo) e 29 ms (publicador Python, conexão e transação).
São caminhos de inicialização diferentes; esses valores não demonstram que o
motor dbt executa DDL centenas de vezes mais devagar.

As quatro métricas e os dois casos adicionais produziram valores iguais com
tolerância absoluta de `1e-7`; o comando falha se houver divergência. Um teste
independente também calcula média, desvio e cobertura diretamente da fixture em Python.

A tem 283 linhas não vazias/não comentadas nos modelos SQL e YAML semântico;
B tem 202 nos quatro YAMLs do catálogo, com metadados. Há mais 89 nas bases e
validação SQL e 79 no publicador/interface Python. Esses escopos não equivalem;
o catálogo descritivo e as bases também são necessários em A. A contagem detalhada
por arquivo está no resultado; não usamos uma soma seletiva como prova de simplicidade.

Mudando o mínimo de **uma** métrica de 10 para 5.000 em cópias isoladas, ambas
suprimiram **a média**: um campo em um arquivo em cada alternativa. Em A,
a contagem e o desvio-padrão não foram alterados; em B, a linha inteira desapareceu.
Isso não comprova propagação equivalente da supressão do grupo. Uma fórmula
reimplementada pelo consumidor pode divergir nas duas. Em B, a interface Python
compara o hash e a versão do catálogo com a publicação no banco e rejeita a leitura
se o catálogo mudou sem republicação; isso também é testado.

O baseline dbt/PostgreSQL tem 60 pacotes e cerca de 96,8 MiB em `site-packages`;
com o CLI MetricFlow, 66 pacotes e 116,7 MiB: aproximadamente **19,8 MiB adicionais**.
O dbt desta bancada já traz bibliotecas do motor MetricFlow entre suas dependências.
O baseline preserva o motor 0.213.0 do lockfile; o CLI exige sua atualização para 0.214.0.
A diferença de ambientes foi confirmada também por imagens locais controladas:
mesmo pai e `COPY` para um caminho isolado, delta de 20.802.104 bytes (19,8 MiB).
O método e os IDs estão em `custo_imagem_controlada` nas evidências. Não representa
tamanho comprimido nem substituição das dependências na imagem de produção. O custo
incremental exato de uma imagem de produção com A não foi medido. B reutiliza as
dependências da imagem existente; PyYAML já estava no lockfile e foi explicitado
como dependência direta, sem mudança de versão nem instalação nova.

## Decisão proposta

Adotar **B**, views em `metricas` com funções parametrizadas e catálogo executável.
Para as quatro métricas fixas, o benefício de composição livre de A não compensa
manter outra representação das definições e adaptar a interface. Os tempos ficaram próximos
na diferença por rede; a escolha não depende de B ganhar toda consulta.
Não materializar as views nesta etapa: não há evidência de necessidade nem custo
aceitável de refresh sobre a carga nacional medido nesta bancada.

A interface Python e a CLI recebem **nome + objeto de filtros**, com o mesmo contrato
SQL documentado em [Métricas](../metricas.md). Publicação transacional depois do dbt,
com funções `SECURITY DEFINER` e `search_path` fixo, sem modificar a origem.
O dono/publicador é `eduinfra_dbt`; orquestrador é membro desse papel. Leitores
recebem execução apenas nas métricas ativas e leitura de `metricas.definicoes`,
sem acesso às views-base. A publicação revoga execução para PUBLIC e concessões
amplas herdadas da 0010. A etapa administrativa normaliza funções antigas antes
da publicação de rotina, conforme o contrato em [Métricas](../metricas.md).
A integração foi exercitada com o snapshot `0d83bc2` da migração 0010 do #110;
a reorganização da CLI desse PR precisa preservar a preparação administrativa.
O #110 concede acesso direto à Gold separadamente: esta fronteira protege a
interface semântica e não substitui a matriz de acesso à Gold.

O painel continua estático para apresentação e Pages. Sua geração deverá ler esta
interface e carregar apenas recortes necessários ao uso; filtros client-side que
recalculam números devem usar o mesmo contrato e ser comparados por testes (#94).
Esta entrega não declara aquela migração concluída. Até lá, a duplicação conhecida
está documentada e pode divergir se o consumidor ignorar o catálogo.

Para ETL reverso, a seleção de prioridade só poderá ser publicada quando o grupo
confirmar os cortes de `prioridade_vistoria`, ainda `rascunho` 0.1.0. A #85 consumirá
a definição aprovada pela mesma interface e cuidará da escrita idempotente no destino.
Uma exportação com regra em rascunho não deve ser apresentada como decisão operacional.

## Consequências e quando rever

Perdemos exploração semântica ad hoc, combinações livres de métricas e recursos de
plataforma gerenciada. Mantemos validação, documentação e compatibilidade da interface
em código próprio. Views consultam a Gold na leitura; durante reconstruções que
substituem tabelas, dependências podem ficar indisponíveis até a republicação.
Sem réplica ou snapshot materializado, este ADR não promete disponibilidade contínua.

Reabrir a decisão se:

- novas equipes precisarem combinar métricas sem escrever SQL;
- consumidores exigirem API remota, autorização por linha ou disponibilidade durante rebuild;
- medições com a Gold nacional violarem o SLA de consulta acordado pelo grupo;
- duplicações voltarem a gerar divergência mesmo com testes e interface;
- o destino de ETL reverso ou as regras de priorização mudarem.

Não foram medidos usabilidade humana, carga nacional, concorrência nem escrita de
ETL reverso. Esses limites não são resultados positivos presumidos desta bancada.

## Histórico de Versões

| Versão | Data | Mudança | Responsável |
| --- | --- | --- | --- |
| 1.0 | 24/11/2026 | Protótipos, medição e decisão proposta | Pedrovargas10 |
| 1.1 | 27/11/2026 | Contrato de acesso, hash SQL e correção do alcance do experimento | Pedrovargas10 |
