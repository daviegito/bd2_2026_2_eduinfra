# Controle de acesso

Responde à issue #86. Até a E3, carga, dbt, CDC, orquestrador e painel usavam
o mesmo `POSTGRES_USER` do Compose, que é superusuário. A migração
`0010_controle_de_acesso.py` cria papéis com privilégio mínimo, aplica recorte
de linha na fila de vistorias e recorte de coluna na tabela `tecnico`. O
superusuário passa a ser usado **apenas** por `migrar`/`reverter`; o
orquestrador não recebe `DATABASE_URL_ADMIN`.

## Matriz papel × objeto × operação

| Papel | Lê | Escreve | Replica | Não alcança |
| --- | --- | --- | --- | --- |
| `eduinfra_carga` | `public` (catálogo, cadastro, vistorias, manifesto), `gold`, `monitoramento` | `INSERT` em tabelas da origem e `monitoramento.frescor_gold` | não | escrita em `gold`; `REPLICATION`; `ALTER`/`DROP` da origem |
| `eduinfra_dbt` | `entrada_transformacao`, `fonte` (opção nula), `item_infraestrutura`, `municipio`, `escola`, `gold` | `CREATE`/`DROP`/`INSERT` só em `gold` (é o dono) | não | escrita em `public`, `entrada_transformacao`, `fonte`, `monitoramento` |
| `eduinfra_cdc` | `vistoria`, `vistoria_item`, slot lógico | avança o slot | sim (`REPLICATION`) | escrita na origem, Gold, superusuário |
| `eduinfra_cdc_bancada` | `vistoria`, `vistoria_item`, `escola`, `municipio`, `item_infraestrutura`, slot lógico | `INSERT` em `tecnico`, `vistoria`, `vistoria_item` | sim (`REPLICATION`) | rotina de ingestão; Gold; superusuário |
| `eduinfra_painel` | `gold`, `metricas` (se existir) | nada | não | `public`, `entrada_transformacao`, `monitoramento` |
| `eduinfra_analise` | `gold`, `municipio`, `escola`, colunas não pessoais de `tecnico`, `metricas` (se existir) | nada | não | `tecnico.nome`, `tecnico.matricula`, escrita, `monitoramento` |
| `eduinfra_tecnico_df` | `fila_vistoria` e `vistoria`/`vistoria_item` da UF `DF` (RLS) | nada | não | `escola`/`municipio` direto; outras UFs |
| `eduinfra_tecnico_sp` | `fila_vistoria` e `vistoria`/`vistoria_item` da UF `SP` (RLS) | nada | não | `escola`/`municipio` direto; outras UFs |
| `eduinfra_orquestrador` | união de `eduinfra_carga` e `eduinfra_dbt` | `fonte`, `entrada_transformacao`; entra como `dbt` na Gold | não | `DATABASE_URL_ADMIN`; superusuário |

Nenhum papel de serviço é superusuário, tem `BYPASSRLS` ou `CREATEDB`. `eduinfra_cdc`
(consumidor de rotina) e `eduinfra_cdc_bancada` (ferramentas de bancada) têm
`REPLICATION`; `eduinfra_carga` não tem.

## Papéis e conexões no Compose

| Serviço | `DATABASE_URL` | Observação |
| --- | --- | --- |
| `carga` | `eduinfra_carga` | `DATABASE_URL_ADMIN` (superusuário) só existe para `migrar`/`reverter` |
| `transformacao` | `eduinfra_orquestrador` | `DATABASE_URL_TRANSFORMACAO=eduinfra_dbt` para o dbt; prepara entrada/fonte como orquestrador |
| `cdc` | `eduinfra_cdc` | `REPLICATION` + `SELECT` nas tabelas publicadas |
| `orquestrador` | `eduinfra_orquestrador` | pertence a carga e dbt; sem `DATABASE_URL_ADMIN` |
| `painel` | `eduinfra_painel` | só leitura da Gold/métricas (`docker compose --profile analitico run painel`) |

O `banco` continua subindo com `POSTGRES_USER` (é o bootstrap do cluster). A
partir daí, `migrar` conecta com `DATABASE_URL_ADMIN`; todo o resto usa os
papéis acima. Para rodar fora do Docker, copie `.env.example` e ajuste
`EDUINFRA_SENHA_PAPEIS` (senha única local dos papéis, usada pela migração, pelo
Compose e pela demonstração).

## Recorte de linha: a fila da UF

A política `vistoria_fila` filtra `vistoria` pela UF da escola via
`public.uf_da_escola`, comparada à UF do técnico (`uf_do_papel`); `vistoria_item_fila`
segue pela vistoria. O técnico consulta a fila por `public.fila_vistoria`, sem
acesso direto a `escola`/`municipio`.

```sql
using (public.uf_da_escola(vistoria.co_entidade) = public.uf_do_papel(current_user))
```

A UF vem de `public.acesso_uf_papel`, um mapa papel → UF alimentado a partir de
`tecnico.lotacao_uf`. A função `uf_do_papel` é `SECURITY DEFINER`: o técnico a
executa, mas não lê nem altera o mapa, então não consegue trocar a própria UF.
O mapa já nasce com `eduinfra_tecnico_df → DF` e `eduinfra_tecnico_sp → SP`;
para um operador real, provisione o papel com a `lotacao_uf` dele.

As políticas permissivas de `eduinfra_carga`/`eduinfra_orquestrador`,
`eduinfra_dbt`/`eduinfra_cdc` (leitura) e `eduinfra_cdc_bancada` mantêm o
pipeline e as ferramentas de bancada enxergando tudo.

## Recorte de coluna: dados pessoais do técnico

`eduinfra_analise` recebe `SELECT (id, lotacao_uf, ativo, registrado_em)` em
`tecnico`. Não há `GRANT SELECT` de tabela inteira, então `nome` e `matricula`
ficam fora do alcance do papel de análise:

```sql
grant select (id, lotacao_uf, ativo, registrado_em) on public.tecnico to eduinfra_analise;
```

## Demonstração

```bash
docker compose run --rm carga demonstrar-acesso
```

O comando conecta com cada papel e registra os quatro casos: leitura permitida,
escrita negada, linha de outra UF invisível e coluna negada. Sai com código 1 se
algum caso divergir. Saída de uma base de desenvolvimento com o Censo 2024
(guardada em `docs/evidencias/controle-de-acesso/saida.txt`):

```text
Demonstração de controle de acesso (issue #86)
[OK] leitura permitida (painel -> gold): esperado SELECT aceito; obtido 181065 linhas
[OK] escrita negada (painel -> municipio): esperado permission denied; obtido permission denied
[OK] linha invisível (técnico -> UF): esperado só a própria UF; obtido DF vê 1325/1325 e 0 da vistoria 144148 de SP; SP vê 30746/30746 (148996 de outras UFs ocultas)
[OK] coluna negada (análise -> tecnico): esperado nome e matricula negados; obtido id/lotacao_uf liberados; nome e matricula negados
4/4 casos como esperado
```

## Testes

```bash
# Declaração: falha se um privilégio for ampliado (não precisa de banco)
uv run pytest tests/test_controle_de_acesso.py -q -m "not integracao"

# Integração: os quatro casos e os atributos dos papéis (exige banco migrado)
uv run pytest tests/test_controle_de_acesso.py -q -m integracao
```

Os testes de declaração leem a própria migração 0010 e garantem, por exemplo,
que `eduinfra_painel` não recebe `INSERT/UPDATE/DELETE` e que `nome`/`matricula`
não entram no recorte de coluna. Os de integração consultam `pg_roles`, tentam
as escritas proibidas e comparam as contagens por UF para provar a invisibilidade
da linha. Se alguém ampliar uma permissão, os negados deixam de falhar e o teste
quebra.

## Validação no Docker

Execução feita no Compose, com os serviços já usando os papéis:

```bash
docker compose run --rm --no-deps carga demonstrar-acesso        # 4/4 casos
docker compose run --rm --no-deps --build transformacao
docker compose run --rm --no-deps --build painel                 # docs/painel.html
docker compose run --rm --no-deps carga demonstrar-cdc           # 5 etapas do CDC
docker compose run --rm --no-deps carga auditar-cdc
```

- `transformacao`: download/ingestão do ENEM, `preparar` da fonte/entrada e
  dbt como `eduinfra_dbt` na Gold. O `dbt build` terminou com **39/39** testes
  e a Gold com 181.065 escolas e 3.078.105 itens de vistoria.
- `painel` exportou `docs/painel.html` lendo só `gold` e a cobertura do ENEM.
- `demonstrar-cdc` confirmou a criação/leitura/avanço do slot como
  `eduinfra_cdc_bancada` (com `REPLICATION`), sem superusuário.
- `auditar-cdc` rodou como bancada e apontou 34 itens perdidos pelo slot que já
  estava `lost` antes da recriação; é o estado herdado do ambiente, não uma
  permissão negada.

## Limites

- A camada `metricas` pertence à entrega da issue #98. Se o esquema existir, a
  migração concede `USAGE`/`SELECT` ao painel e à análise; sem ele, só a Gold é
  lida.
- O painel lê só `gold` (+ `metricas` opcional). A cobertura do ENEM no painel
  vem de `gold.stg_enem_escola_ano`.
- `construir-gold` (opção nula) roda como `eduinfra_orquestrador` e entra como
  `eduinfra_dbt` só na etapa que escreve a Gold; reaplica `USAGE`/`SELECT` para
  carga/painel/análise depois de recriar `gold`.
- As ferramentas de bancada do CDC (`demonstrar-cdc`, `comparar-ingestao`,
  `medir-cdc`, `medir-recarga`, `auditar-cdc`) usam `DATABASE_URL_CDC_BANCADA`;
  o consumidor de rotina usa `eduinfra_cdc`.
- Em produção, troque `EDUINFRA_SENHA_PAPEIS` por segredos distintos por papel e
  gerencie-os fora do repositório.
