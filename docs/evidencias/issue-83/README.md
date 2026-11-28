# Comparação da camada semântica — #83

Data: 24/11/2026. PostgreSQL 16.14 em contêiner descartável, Python 3.12.3,
dbt-core 1.12.6, dbt-postgres 1.11.0 e dbt-metricflow 0.16.0.
A máquina também executava outros serviços. Estes números caracterizam esta bancada,
sem estimar a latência na carga nacional.

## Reproduzir

Pré-requisitos: Docker, `uv`, dependências de desenvolvimento do projeto instaladas e
permissão para criar o banco descartável e instalar as dependências **de bancada**.
As credenciais abaixo são exclusivas de dados sintéticos. Não use o banco da origem.

```bash
docker run -d --name eduinfra83-bancada \
  -p 127.0.0.1:55483:5432 \
  -e POSTGRES_USER=eduinfra83 -e POSTGRES_DB=eduinfra83 \
  -e POSTGRES_PASSWORD=validacao83_local postgres:16
uv venv /tmp/eduinfra83-metricflow
uv pip install --python /tmp/eduinfra83-metricflow/bin/python -r docs/evidencias/issue-83/requirements.txt
uv venv /tmp/eduinfra83-dbt-lock-base
uv pip install --python /tmp/eduinfra83-dbt-lock-base/bin/python 'dbt-core==1.12.6' 'dbt-postgres==1.11.0' 'metricflow==0.213.0'
```

Para o custo de imagem, use o nome de uma imagem **já existente** do projeto,
gerada pelo Compose; ajuste `--imagem-base` ao nome local. A opção é facultativa
para medir só SQL e dependências, mas foi executada na evidência desta entrega.

Aguarde `docker exec eduinfra83-bancada pg_isready -U eduinfra83` indicar pronto.
Na raiz do repositório, esta é a medição em **um comando**:

```bash
SEMANTICA_BANCADA_DSN=postgresql://eduinfra83:validacao83_local@127.0.0.1:55483/eduinfra83 \
uv run --no-sync python docs/evidencias/issue-83/comparar.py \
  --bin-metricflow /tmp/eduinfra83-metricflow/bin \
  --bin-dbt-base /tmp/eduinfra83-dbt-lock-base/bin \
  --imagem-base bd2_2026_2_eduinfra-carga \
  --preparar --repeticoes 5 --saida docs/evidencias/issue-83/resultados.json
```

O programa recusa banco diferente de `eduinfra83` e host diferente de loopback.
`--preparar` cria a fixture somente se a Gold ainda não existir: não limpa nem
substitui dados preexistentes. A fixture tem 6.000 escolas sintéticas, 5.000 linhas
ENEM correspondentes, 100 códigos mascarados sem correspondência, notas nulas,
as cinco situações de infraestrutura e duas UFs. Não contém dados pessoais.
A execução republica views/funções da bancada e cria uma cópia temporária do
protótipo para o experimento de mudança. Não acessa slot, WAL nem banco da origem.

## Método

- A: modelos semânticos e métricas reais do dbt, compiladas pelo CLI MetricFlow;
  `metricflow/models/metricas.yml` contém as quatro definições e medidas auxiliares.
- B: views PostgreSQL `metricas.base_analise` e `base_cobertura`, com funções
  parametrizadas publicadas a partir dos quatro YAMLs ativos do catálogo.
  Funções permitem nome e filtros sem criar uma view para cada combinação.
- As bases SQL de junção são compartilhadas pelos protótipos. A comparação mede a
  agregação semântica sobre a mesma população, não duas implementações da Gold.
- Cinco amostras após aquecimento, na mesma conexão PostgreSQL. O SQL compilado de
  A é executado diretamente, sem somar a inicialização do CLI à latência SQL.
  Compilação fria do CLI é registrada separadamente. B inclui validação no servidor.
- Atualização mede o processo `dbt run` completo de A versus a função Python de
  publicação de B, incluindo conexão e transação. Inclui diferenças de inicialização;
  não mede processamento incremental nem construção da Gold, já compartilhada.
- Igualdade das quatro métricas é obrigatória: execução falha se qualquer campo
  divergir em mais de `1e-7`. Há também média de matemática no DF e população vazia
  no AC. Os SQLs efetivamente compilados são preservados em `sql_compilado/`.
- Mínimo da média alterado de 10 para 5.000 em cópias das duas definições: ambas
  passam a suprimir a média. Muda um campo em um arquivo em cada alternativa,
  mas a contagem e o desvio continuam em A; em B a linha inteira desaparece.
  Não é uma comprovação de supressão equivalente do grupo completo.
  A mudança não é publicada no catálogo de produção nem no catálogo da bancada.

## Resultados e decisão

Amostras e resultados completos em [resultados.json](resultados.json).
O [ADR 0006](../../adr/0006-consumo-etl-reverso.md) registra a recomendação e os
limites, incluindo as diferenças entre tamanho instalado e tamanho de imagem.

`linhas_nao_vazias` conta linhas sem branco ou comentário por arquivo e escopo:
A inclui modelos SQL e YAML semântico; B inclui o catálogo com metadados; bases SQL,
validação, publicador e interface são informados separadamente. Esses escopos não
são equivalentes: não somar seletivamente para concluir que uma ferramenta tem
menos manutenção. A também precisa do catálogo descritivo e das bases compartilhadas.

O custo instalado compara dois venvs isolados resolvidos na mesma sessão, um com
dbt/PostgreSQL e outro também com MetricFlow. É soma de bytes aparentes em
`site-packages`; cache de bytecode pode variar entre execuções. Não é tamanho
comprimido de imagem. [dependencias.json](dependencias.json) identifica os seis
pacotes adicionais e a atualização do motor de 0.213.0 (lockfile) para 0.214.0.

A escolha mantém uma definição executável por métrica e evita dependência nova
na imagem: PyYAML já está no lockfile pelo Dagster e passa a ser dependência direta.
Recalcular fórmulas no JavaScript ou SQL de um consumidor ainda permite divergência
em ambas as alternativas. Por isso a interface pública é parte da decisão; a
migração do painel em desenvolvimento pertence à #94, sem alterar a branch do colega.

O custo em imagem também é registrado em `custo_imagem_controlada`:
mesmo pai local e mesmo caminho de destino, copiando os ambientes já instalados
para `/opt/bancada/site-packages`. Não baixa ou instala pacotes nem muda a imagem
de produção; cria tags locais `eduinfra83-dependencias-*`. É um experimento de
camadas isoladas: o delta compara os dois ambientes, sem prometer o tamanho final
de uma futura imagem de produção. O comando não remove imagens ou arquivos.

## Testes de contrato

```bash
SEMANTICA_BANCADA_DSN=postgresql://eduinfra83:validacao83_local@127.0.0.1:55483/eduinfra83 \
uv run --no-sync pytest tests/test_semantica.py tests/test_metricas.py -q
uv run --no-sync pytest tests/test_orquestrador.py -q
```

Cobrem SQL/Python, valores esperados calculados independentemente em Python, filtros
inválidos inclusive sobre população vazia, supressão, denominador vazio, rascunhos
não publicados e mudança de catálogo sem republicação. A comparação A/B é verificada
pelo comando da bancada, que exige o CLI experimental fora da imagem de produção.

## Correspondência com o checklist

| Item da #83 | Evidência |
| --- | --- |
| Protótipo A e suporte PostgreSQL | projeto `metricflow/`, SQL compilado e resultados reais |
| Protótipo B | views em `metricas/sql/` e funções parametrizadas geradas do catálogo |
| Linhas, consulta, atualização, custo e mudança | `resultados.json`, `dependencias.json`, `imagens.json` |
| Decisão com ganhos, perdas e revisão | ADR 0006, proposto para revisão humana |
| Interface única | `eduinfra.semantica.consultar`, CLI e contrato em `docs/metricas.md` |
| Atualização depois do dbt | asset `publicar_metricas` e teste do grafo Dagster |
| Igualdade por duas rotas, com teste | comparação A/B e contrato Python/SQL; oráculo independente |
| Medição com um comando | comando reproduzível desta página |

A adoção pela Squad depende da revisão do PR; a migração do consumidor do painel e
a escrita operacional permanecem nas respectivas entregas, sem bloquear a publicação
da interface. Os limites da bancada constam do ADR, sem alegar validação nacional.

## Revisão de 27/11: contrato e controle de acesso

Os números de `resultados.json` são da medição original de 24/11. Não representam
nova medição da implementação de permissões/hash revisada; a conclusão sobre a
mudança de regra foi restringida à média, sem alterar os tempos coletados.

O CI e a bancada conjunta usam a migração 0010 do #110 no commit
`0d83bc291bf49a3c6c28842cfb8e2ed305c6bb84`. Não copiam a migração para a aplicação.
Em um PostgreSQL **descartável**, com banco chamado `eduinfra83`, a preparação é:

```bash
git show origin/feat/controle_acesso:alembic/versions/0010_controle_de_acesso.py > /tmp/controle_acesso_0010.py
uv run --no-sync python tests/preparar_semantica.py \
  --dsn postgresql://eduinfra83:validacao83_local@127.0.0.1:55483/eduinfra83 \
  --migracao-acesso /tmp/controle_acesso_0010.py
SEMANTICA_BANCADA_DSN=postgresql://eduinfra83:validacao83_local@127.0.0.1:55483/eduinfra83 \
uv run --no-sync pytest tests/test_semantica.py tests/test_semantica_cli.py tests/test_metricas.py tests/test_orquestrador.py -q -rs
```

O preparador recusa outros bancos/hosts e só cria a fixture se a Gold não existe.
A variável opcional `SEMANTICA_PUBLICADOR_LEGADO` recebe um arquivo Python com o
publicador anterior para exercitar a atualização; o CI extrai o commit `8e87397`
num caminho temporário antes de aplicar a 0010. A etapa administrativa chama
`preparar_acesso`, e o publicador de rotina passa a usar o dono dbt.

Há oráculos Python independentes para as quatro métricas, testes do limite 9/10
na média e nos dois lados da diferença, atomicidade da publicação, limpeza de
função desativada, hash dos SQLs, município inexistente, CLI, papéis de leitura e
Gold sem ENEM. Para a disponibilidade sem ENEM, o perfil é o fechamento anual;
a geração original com ENEM continua usando a referência histórica da prova.

Ao integrar o #110, preservar a chamada administrativa após Alembic no novo
módulo `origem/comandos.py:migrar`. Sem isso, instalações antigas com funções
pertencentes ao administrador não podem ser atualizadas pelo papel de rotina.

### Resultados da revisão

- [Contrato conjunto e repetição da preparação](validacao-contrato-103.txt):
  PostgreSQL novo, publicação legada, 0010 aplicada uma vez pelo Alembic,
  segunda preparação sem reaplicar políticas; 63 testes e 42 subtestes aprovados,
  nenhum skip.
- [Suíte sem integração](validacao-unitarios-103.txt): 232 aprovados, sete skips
  (três de PyIceberg e quatro de Node) e 42 subtestes. Rodou em imagem existente,
  usando Hypothesis e extensões DuckDB já disponíveis em cache, sem instalação.
  O checkout entrou no PYTHONPATH também dos subprocessos.
- Os 17 testes do painel passaram depois no ambiente local com Node, sem skips.
- `mkdocs build --strict` e `git diff --check` aprovados. O HTML antigo local do
  painel foi usado no build; não foi reconstruída a Gold nacional nesta revisão.
- A revisão independente apontou a execução duplicada da 0010 no preparador;
  após a correção não apontou outro achado importante ou crítico nos trechos revistos.
- Nenhum contêiner desta validação ficou rodando.
