# Testes

Execute os comandos na raiz do repositório. Os testes sem banco e os testes de
integração têm pré-requisitos diferentes; uma execução com testes ignorados não
comprova o funcionamento completo da integração.

## Preparar as dependências

Com Python e [uv](https://docs.astral.sh/uv/) disponíveis:

```bash
uv sync --group dev
```

## Testes sem PostgreSQL

```bash
uv run pytest -m "not integracao" -q
```

Esse grupo cobre transformação, decodificação de `pgoutput`, eventos, janelas e
operações analíticas em arquivos temporários. As leituras Delta pelo DuckDB
podem exigir internet para baixar a extensão; verifique os motivos de eventuais
`skipped` com `-rs`.

## Testes de integração

Use um banco de desenvolvimento descartável. Os testes de CDC e janelas
inserem vistorias na origem; os de ingestão criam e removem bancos próprios e
precisam de permissão para isso.

1. Prepare o ambiente conforme o [guia de execução](execucao.md).
2. Aguarde o serviço `carga` terminar com sucesso.
3. Pause o consumidor contínuo para não disputar o fluxo com os testes.
4. Exporte o `DATABASE_URL` do `.env`, apontando para a porta publicada no host.

```bash
docker compose stop cdc
set -a
source .env
set +a
uv run pytest -m integracao -q -rs
docker compose start cdc
```

Sem `DATABASE_URL`, vários testes são ignorados. Alguns também exigem a origem
populada. O comando para executar todos os testes é `uv run pytest -q -rs`,
respeitando os mesmos pré-requisitos e a pausa do consumidor.

## Testes por funcionalidade

| Funcionalidade | Arquivos principais |
| --- | --- |
| Ingestão e recuperação da Silver | `tests/test_ingestao.py` |
| CDC e reentrega | `tests/test_pgoutput.py`, `tests/test_fluxo_cdc_integracao.py` |
| Eventos e deduplicação | `tests/test_eventos.py` |
| Janelas e atraso | `tests/test_janelas.py`, `tests/test_janelas_integracao.py` |
| Camada analítica | `tests/test_camada_analitica.py` |
| Compressão | `tests/test_comparacao_compressao.py` |
| Histórico insert-only | `tests/test_insert_only.py` |

## Verificações opcionais

Para testes baseados em propriedades e fuzzing, instale os extras:

```bash
uv sync --group dev --extra dev_extra
uv run python tests/fuzz_example_atheris.py
```

O fuzzing executa continuamente até ser interrompido. Ele complementa a suíte;
não é pré-requisito para consultar a documentação ou executar o projeto.

## Amostras sintéticas

Em banco de desenvolvimento com as migrações aplicadas:

```bash
docker compose run --rm carga gerar_amostra --escolas 1000
```

O gerador acrescenta escolas e vistorias sintéticas, com até cinco itens por
vistoria. Ele **não é idempotente**: repetir o comando acrescenta vistorias.
Não use a amostra como se fosse uma medição da base real do Censo e não versione
arquivos de dados gerados.

## Validar a documentação

```bash
uv run mkdocs build --strict --site-dir /tmp/eduinfra-site
```

A validação gera o site e acusa links internos quebrados. A renderização dos
diagramas também deve ser conferida no navegador.
