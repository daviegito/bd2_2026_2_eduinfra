# Como executar e demonstrar

Execute os comandos a partir da raiz do repositório. O fluxo padrão sobe o
PostgreSQL, prepara os dados do Censo e inicia o consumidor CDC.

## Preparar o ambiente

Pré-requisitos:

- Docker com Docker Compose v2 (`docker compose`, sem hífen);
- acesso à internet, porque a carga baixa cerca de 34 MB do INEP;
- espaço para imagens Docker, banco, arquivos de entrada e destinos analíticos;
  os 445 MB medidos na E1 correspondem apenas ao banco, não ao ambiente completo.

O resto vem nas imagens: PostgreSQL 16 (`pgvector/pgvector:pg16`) e Python 3.12
com [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/daviegito/bd2_2026_2_eduinfra.git
cd bd2_2026_2_eduinfra
cp .env.example .env
# Edite .env: ajuste POSTGRES_PASSWORD e a senha correspondente em DATABASE_URL.
docker compose up --build
```

O serviço `carga` aplica as migrações, ingere o Censo 2024, publica a linha de
base analítica, imprime a caracterização e verifica as contagens da tabela
derivada da Silver. Ao terminar com código 0, libera o serviço `cdc`, que
acompanha novas vistorias a cada dois segundos. `banco` e `cdc` continuam no ar.
O tempo depende da máquina, da rede e do cache das imagens.

```bash
docker compose ps
docker compose logs --tail=30 cdc
```

A primeira leitura Delta pelo DuckDB pode baixar sua extensão e exigir internet.

Se a porta 5432 já estiver em uso, mude `PORTA_POSTGRES` no `.env`.

Para conferir a base populada:

```bash
docker compose exec banco psql -U eduinfra -d eduinfra \
  -c "select * from escola_estado_atual where co_entidade = 53002580;"
```

Para parar os serviços preservando os volumes:

```bash
docker compose down
```

??? warning "Recomeçar com volumes vazios"

    `docker compose down -v` remove os volumes deste projeto Compose, incluindo
    banco, arquivos e destinos analíticos. Use apenas quando quiser descartar
    esses dados e reconstruir o ambiente.

## Comandos da CLI

A imagem expõe a CLI `eduinfra`. Com o banco no ar:

| Comando | O que faz |
| --- | --- |
| `docker compose run --rm carga migrar` | Aplica as migrações |
| `docker compose run --rm carga ingerir` | Ingere o Censo com manifesto e publicação da Silver |
| `docker compose run --rm carga caracterizar` | Exibe contagens e tamanhos da origem |
| `docker compose run --rm carga tudo` | Migra, ingere, publica a linha de base e verifica a tabela analítica |
| `docker compose run --rm carga publicar-analitica` | Republica a tabela Delta derivada da Silver |
| `docker compose run --rm carga demonstrar-cdc` | Demonstra captura, interrupção e reentrega sem duplicação |
| `docker compose run --rm carga demonstrar-atraso` | Demonstra o tratamento de eventos atrasados |
| `docker compose run --rm carga comparar-ingestao` | Compara captura incremental com captura pelo slot, sem medir a escrita Delta |
| `docker compose run --rm carga comparar-compressao` | Compara formatos e compressão |
| `docker compose run --rm carga comparar-particionamento` | Compara estratégias de particionamento |

As demonstrações de CDC e atraso e a comparação de ingestão inserem dados de
teste. Execute-as em um ambiente de desenvolvimento descartável. Para uma
demonstração manual do CDC, pause o consumidor contínuo antes e retome depois:

```bash
docker compose stop cdc
docker compose run --rm carga demonstrar-cdc
docker compose start cdc
```

Para rodar fora do Docker, com [uv](https://docs.astral.sh/uv/) e o banco do
compose publicado na porta do `.env`:

```bash
uv sync
set -a && source .env && set +a
uv run eduinfra tudo
```

## Demonstrações e medições

| Objetivo | Comando da CLI | Evidência documentada |
| --- | --- | --- |
| Retomada e deduplicação | `demonstrar-cdc` | [CDC e semântica de entrega](cdc-e-semantica-de-entrega.md#demonstracao) |
| Vistoria digitada com atraso | `demonstrar-atraso` | [Dados atrasados](dado-atrasado.md#demonstracao) |
| Formato e compressão | `comparar-compressao --repeticoes 15` | [Compressão](serializacao-compressao.md#resultados) |
| Organização dos arquivos | `comparar-particionamento --repeticoes 15` | [Camada analítica](camada-analitica.md#resultados) |
| Alternativas de captura | `comparar-ingestao` | [Comparação de ingestão](cdc-e-semantica-de-entrega.md#medicao-slot-logico-contra-recarga-incremental) |

Use o prefixo `docker compose run --rm carga` antes do comando da CLI.
As medições publicadas descrevem os ambientes em que foram obtidas; os tempos
podem variar em outra máquina. A comparação de ingestão mede captura, sem a
escrita no Delta. Para a suíte automatizada, consulte [Testes](testes.md).
