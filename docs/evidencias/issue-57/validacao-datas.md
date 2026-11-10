# Issue #57 — Validação de datas de vistoria

Validação local em 8 de novembro de 2026, na branch `fix/datas-vistoria`,
base inicial `7529826`, sincronizada com `e50f6a6` após a integração do PR #64. PostgreSQL 16 em contêiner dedicado e bancos descartáveis;
nenhum banco de uso pessoal ou compartilhado foi migrado.

## Regra aprovada

`ocorrido_em` deve ser finito e estar no intervalo UTC
`[0001-01-01 00:00:00+00, 10000-01-01 00:00:00+00)`.
São os anos 1 a 9999 representáveis pelo `datetime` do Python. O recorte
analítico do Censo Escolar/ENEM 2024 não determina o limite de uma vistoria;
não foi criado um corte arbitrário em 2007 ou 2024. O check
`vistoria_evento_antes_do_registro` permanece ativo.

A migração 0009, sucessora do manifesto ENEM 0008, bloqueia inserções durante a verificação e criação do check.
Se encontrar histórico inválido, informa quantidade e até dez exemplos com
ID e data em UTC, aborta a transação e mantém a revisão 0008. Não executa
`UPDATE`, `DELETE` nem correções automáticas nas tabelas insert-only.

A publicação valida todas as vistorias (inclusive sem itens), antes da conversão
para Arrow e da escrita no Delta. Validação e exportação usam uma única
transação da extensão PostgreSQL do DuckDB; um teste insere um evento inválido
entre as consultas e comprova que ele não aparece no snapshot já validado.
A leitura fixa UTC para que o ano 0001 não transborde ao converter em outro fuso.
Referência: [extensão PostgreSQL do DuckDB](https://duckdb.org/docs/stable/core_extensions/postgres/overview.html).

## Verificação do histórico populado

Foi restaurada uma cópia local da carga nacional de Censo Escolar 2024 com
vistorias sintéticas. A consulta abaixo foi executada antes e depois do upgrade:

```sql
SELECT count(*), min(ocorrido_em)::text, max(ocorrido_em)::text,
       md5(string_agg(md5(to_jsonb(v)::text), '' ORDER BY id)),
       count(*) FILTER (WHERE NOT (
           isfinite(ocorrido_em)
           AND ocorrido_em >= timestamptz '0001-01-01 00:00:00+00'
           AND ocorrido_em < timestamptz '10000-01-01 00:00:00+00'
       )) AS invalidas
FROM vistoria v;
```

A tabela abaixo registra a rodada inicial, quando a migração desta issue ainda
se chamava 0008 (antes da integração do manifesto ENEM).

| Verificação | Antes | Depois |
| --- | --- | --- |
| Vistorias | 181.093 | 181.093 |
| Menor data UTC | 2024-05-29 00:00:00 | 2024-05-29 00:00:00 |
| Maior data UTC | 2026-11-08 15:51:31.959737 | 2026-11-08 15:51:31.959737 |
| Impressão do histórico completo | `7afa57eaec1dc40d0215b290aa906dd9` | `7afa57eaec1dc40d0215b290aa906dd9` |
| Datas inválidas | 0 | 0 |
| Revisão Alembic | 0007 | 0008 |

## Checklist e critérios de aceite

- [x] Limite inferior definido e justificado pela representação técnica em UTC.
- [x] Histórico conferido antes da migração, com comparação integral após o upgrade.
- [x] Regra em nova migração Alembic, sem alterar linhas insert-only.
- [x] `publicar-analitico --forcar` rejeita datas inválidas e preserva arquivos,
  versão e conteúdo de um Delta existente; sem Delta anterior, não cria a tabela.
- [x] Seção de limites de `metricas-cdc.md` e ADR 0007 atualizados.
- [x] INSERT de `-infinity` e data a.C. falha com `CheckViolation` do novo check.
- [x] Migração do zero, banco populado válido e rollback do legado inválido testados.
- [x] Nenhuma partição inválida é criada na publicação forçada.

## Testes e reprodução

Com as dependências do projeto já disponíveis e `DATABASE_URL` apontando para um
PostgreSQL **descartável**, com `wal_level=logical` e permissão para criar bancos:

```bash
PYTHONPATH=src python -m pytest tests/test_datas_vistoria_integracao.py -q
PYTHONPATH=src python -m pytest \
  tests/test_resiliencia_cdc_integracao.py tests/test_eventos.py -q
PYTHONPATH=src python -m pytest --ignore=tests/test_transformacao_property.py -q
```

Os 26 testes específicos cobrem os dois infinitos, datas a.C., ano 10000,
os anos 0001 e 9999, histórico anterior a 2007, a regra de evento antes do registro,
o diagnóstico com ID, a manutenção da revisão após erro, a reversão do check,
a carga válida no Delta, a preservação do Delta durante falha e o snapshot
sob inserção concorrente. Os testes históricos de DLQ removem o check apenas
no banco descartável do cenário, para simular explicitamente uma origem legada.

Resultados da rodada inicial: **26 testes específicos passaram**; na suíte mais ampla,
**231 passaram e 5 foram pulados** (57,82 s). Os cinco são os testes de
frescor da Gold, pulados porque a Gold não foi construída nesse banco de origem;
a conferência isolada com `-rs` confirmou o motivo (8 passaram e 5 pulados).
A documentação passou em
`mkdocs build --strict` e `git diff --check` não apontou erros.
A sincronização posterior do PR #64 trouxe apenas documentação/evidências e
`.gitignore`; os arquivos de aplicação e testes verificados não mudaram.

Na rodada inicial, a suíte de propriedades foi excluída porque a dependência opcional `hypothesis`
não estava instalada. A validação final usa Hypothesis em ambiente isolado,
com instalação aprovada, sem modificar as dependências versionadas.

## Escopo de integração

A integração final usa a `main` em `7f6ef47`, com ENEM (#69), fonte canônica
(#70), correção de janelas (#71) e retenção do CDC (#66). A migração do ENEM
ocupa 0008; a regra de datas passou para 0009, com `down_revision = "0008"`.
O conflito em `eventos.py` preserva o filtro e o cast para `ESQUEMA` da fonte
canônica junto da validação, snapshot único e UTC. O registro de uso de IA
preserva as entradas dos dois lados. Não incorpora as branches de outros PRs
nem altera o trabalho da #48.

A #58 (recuperação e tratamento da fila) continua separada. Estes testes não
provam um fluxo de recuperação da DLQ e não fecham aquela issue.

## Integração final — 09/11/2026

A migração 0009 foi aplicada após a 0008 do ENEM num banco novo de teste,
com o recorte público SP/DF/GO/MT/MS do Censo 2024. A base não é o banco pessoal
e não usa os volumes do painel. Hypothesis 6.170.15 foi instalado num ambiente
separado em `/tmp`, sem alterar `pyproject.toml` ou `uv.lock`.

| Verificação do upgrade | Antes (0008) | Depois (0009) |
| --- | --- | --- |
| Vistorias | 41.380 | 41.380 |
| Impressão integral do histórico | `c734445bd83568e0177645dfaeee6f7e` | `c734445bd83568e0177645dfaeee6f7e` |
| Datas inválidas | 0 | 0 |

A construção SQL da Gold confirmou 703.460 eventos na origem e na fonte
canônica, sem chaves exclusivas de um dos lados, e materializou as seis tabelas.
O histórico foi comparado antes de rodar os testes que inserem vistorias sintéticas.
`alembic heads` informa uma única revisão: `0009 (head)`.

A rodada direcionada, após a integração, terminou com **48 testes aprovados**
em 46,58 s: datas, resiliência, eventos e propriedades. Os cenários de datas
partem agora da revisão 0008 e verificam que o rollback do legado inválido
mantém essa revisão, sem desfazer o manifesto do ENEM.

A suíte completa foi executada com a Gold disponível e com Hypothesis:

```text
275 passed, 11 warnings in 94.07s (0:01:34)
```

Não houve falhas nem skips. Os cinco testes de frescor antes pendentes
foram executados, assim como a suíte de propriedades. As onze advertências
são de APIs antigas de particionamento em `comparacao_latencia.py`, fora
do escopo desta issue. `mkdocs build --strict` e `git diff --check` passaram.

Para reproduzir a suíte completa com Hypothesis temporário, sem alterar os
arquivos de dependências, use um banco descartável populado com Gold:

```bash
uv run --with hypothesis pytest -q -rs
```

Estas evidências substituem as pendências da rodada inicial. A integração
não depende do merge do PR #72: a Gold SQL da main já permite validar o
contrato de frescor exigido aqui. Não prova a recuperação da DLQ da #58.
