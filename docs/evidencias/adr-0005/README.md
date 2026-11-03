# Evidências do ADR 0005

A mesma Gold do PR #55 em três alternativas, sobre um recorte real do Censo 2024, para o
[ADR 0005](../../adr/0005-ferramenta-de-transformacao.md).

| Arquivo | O que é |
| --- | --- |
| `preparar_amostra.sh` | cria o banco `eduinfra_adr0005` com as escolas de SP e do Centro-Oeste (41.380 escolas, 703.936 itens) |
| `dbt/` | projeto dbt: os 7 modelos, as fontes e os 12 testes |
| `sqlmesh/` | projeto SQLMesh: os 7 modelos com audits, o audit de relacionamento e o macro de índice |
| `medir.sh` | reconstrói a Gold com cada alternativa, 3 vezes, e imprime o tempo de cada execução |
| `comparar_resultados.sh` | compara duas Golds tabela a tabela, por contagem e pela soma dos hashes das linhas |
| `resultados.txt` | saídas brutas das três rodadas de 03/11/2026 |

O SQL dos modelos foi gerado das constantes de `src/eduinfra/modelo_analitico.py`, sem
edição: só mudam as referências entre tabelas (`ref()` no dbt, `gold_sqlmesh.` no
SQLMesh) e os cabeçalhos de cada ferramenta.

## Como reproduzir

Da raiz do repositório, com o serviço `banco` no ar, a origem carregada e o `.env`
preenchido:

```bash
bash docs/evidencias/adr-0005/preparar_amostra.sh

export PGHOST=localhost PGPORT=$PORTA_POSTGRES PGDATABASE=eduinfra_adr0005 DO_NOT_TRACK=1
(cd docs/evidencias/adr-0005/sqlmesh && uv run --with sqlmesh==0.236.4 --with psycopg2-binary sqlmesh plan --auto-apply --no-prompts)

bash docs/evidencias/adr-0005/medir.sh
bash docs/evidencias/adr-0005/comparar_resultados.sh gold gold_dbt
bash docs/evidencias/adr-0005/comparar_resultados.sh gold gold_sqlmesh
```

O `medir.sh` usa `uv run --with` para as duas ferramentas, sem mexer no `pyproject.toml`.
`ALTERNATIVAS="B"` mede só o dbt, por exemplo. Cada Gold do recorte ocupa cerca de 230 MB.
Ao terminar:

```bash
docker compose exec banco psql -U eduinfra -d eduinfra -c "drop database eduinfra_adr0005 with (force)"
```
