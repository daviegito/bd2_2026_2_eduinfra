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

A migração 0008 bloqueia inserções durante a verificação e criação do check.
Se encontrar histórico inválido, informa quantidade e até dez exemplos com
ID e data em UTC, aborta a transação e mantém a revisão 0007. Não executa
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

Resultados: **26 testes específicos passaram**; na suíte mais ampla,
**231 passaram e 5 foram pulados** (57,82 s). Os cinco são os testes de
frescor da Gold, pulados porque a Gold não foi construída nesse banco de origem;
a conferência isolada com `-rs` confirmou o motivo (8 passaram e 5 pulados).
A documentação passou em
`mkdocs build --strict` e `git diff --check` não apontou erros.
A sincronização posterior do PR #64 trouxe apenas documentação/evidências e
`.gitignore`; os arquivos de aplicação e testes verificados não mudaram.

A suíte de propriedades foi excluída porque a dependência opcional `hypothesis`
não está instalada; nenhuma dependência foi instalada nesta tarefa.

## Escopo de integração

A conferência dos PRs #61, #63, #64, #65 e #66 não encontrou outra
migração 0008 nem mudanças concorrentes em `eventos.py`. Há sobreposição de
registro no `AI-USAGE.md`; a entrada do PR #64 foi preservada na sincronização. Não se alteram os
arquivos funcionais desses PRs nem o trabalho local da #48.

A #58 (recuperação e tratamento da fila) continua separada. Estes testes não
provam um fluxo de recuperação da DLQ e não fecham aquela issue.
