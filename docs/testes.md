# Guia de Testes e Amostras de Dados

Este documento instrui como configurar o ambiente, executar as suítes de testes automatizados e gerar amostras sintéticas para desenvolvimento e avaliação de desempenho.

## Configuração e Execução de Testes

### 1. Instalar Dependências de Desenvolvimento

Para instalar as dependências de testes (Pytest) utilizando o uv:

```
uv sync --group dev
```

Caso deseje incluir os pacotes para testes baseados em propriedades e Fuzzing:

```
uv sync --group dev --extra dev_extra
```

### 2. Executar a Suíte de Testes

Para rodar os testes unitários e de integração:

```
uv run pytest -q
```

### 3. Executar o Teste de Fuzzing

Para executar a verificação contínua de resiliência e validação de arquivos CSV:

```
uv run python tests/fuzz_example_atheris.py
```

## Geração de Amostras para Desenvolvimento e Performance

A geração de amostras sintéticas é útil para realizar testes funcionais rápidos, validações de carga ou benchmarks sem a necessidade de baixar e processar toda a base pública.

### 1. Execução Local via uv

# certifique-se de exportar DATABASE_URL ou usar .env
```
uv run eduinfra gerar_amostra --escolas 1000
```

### 2. Execução via Docker Compose

Para popular o banco de dados em execução no container:

```
docker compose up --build -d
docker compose run --rm carga gerar_amostra --escolas 1000
```

Observação: a amostra é sintética e serve para testes funcionais e benchmarks locais; não comite amostras grandes no repositório.