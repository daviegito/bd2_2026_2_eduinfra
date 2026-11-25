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
inserem vistorias na origem; os de ingestão e de resiliência do CDC criam e
removem bancos próprios e precisam de permissão para isso.

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

Para verificar o sistema contra defeitos de dados sujos nas tabelas da camada Gold, execute o script interativo de demonstração:

```bash
docker compose run --rm --entrypoint uv carga run --no-sync python tests/gold/rodar_demonstracao.py
```
O script injetará dados fora do domínio esperado (ERRO_DE_DOMINIO), comprovará que a auditoria quebra a pipeline detectando o erro e, em seguida, fará o rollback automático.

## Testes por funcionalidade

| Funcionalidade | Arquivos principais |
| --- | --- |
| Ingestão e recuperação da Silver | `tests/ingestao/test_censo.py` |
| ENEM: agregação, cobertura e ingestão idempotente | `tests/lake/test_silver_enem.py`, `tests/ingestao/test_enem.py` |
| CDC e reentrega | `tests/cdc/test_pgoutput.py`, `tests/cdc/test_fluxo_cdc.py`, `tests/cdc/test_fluxo_cdc_integracao.py` |
| Métricas do CDC | `tests/cdc/test_telemetria_cdc.py`, `tests/cdc/test_auditoria_cdc.py`, `tests/experimentos/test_comparacao_latencia.py`, `tests/experimentos/test_comparacao_latencia_integracao.py` |
| Fila de erros e desvio de esquema | `tests/cdc/test_contrato_cdc.py`, `tests/cdc/test_resiliencia_cdc_integracao.py` |
| Consulta otimizada | `tests/experimentos/test_otimizacao_consulta.py`, `tests/experimentos/test_otimizacao_consulta_integracao.py` |
| Eventos e deduplicação | `tests/cdc/test_eventos.py` |
| Janelas e atraso | `tests/cdc/test_janelas.py`, `tests/cdc/test_janelas_integracao.py` |
| Fonte única de eventos: mesmo evento pelos dois caminhos, evento atrasado e Gold lendo a fonte | `tests/cdc/test_fonte_de_eventos.py`, `tests/cdc/test_fonte_de_eventos_integracao.py`, `tests/experimentos/test_modelo_analitico.py` |
| Camada analítica | `tests/lake/test_camada_analitica.py` |
| Compressão | `tests/experimentos/test_comparacao_compressao.py` |
| Histórico insert-only | `tests/origem/test_insert_only.py` |
| Definições de métricas  | `tests/test_metricas.py` |
| Frescor e SLA da Gold | `tests/gold/test_frescor.py`, `tests/gold/test_frescor_integracao.py` (exige `construir-gold` antes) |
| Tabelas de consumo da Gold, construídas pelo dbt com 60 escolas de resultado conhecido | `tests/gold/test_gestao_dbt.py` |
| Painel lendo só a Gold de consumo, frases geradas e proteção contra script no JSON | `tests/consumo/test_painel.py` |
| Comandos da CLI, dependência entre pacotes e caminhos citados nos documentos | `tests/test_cli.py`, `tests/test_estrutura_do_codigo.py`, `tests/test_verificar_caminhos.py` |

## Verificações opcionais

Para testes baseados em propriedades e fuzzing, instale os extras:

```bash
uv sync --group dev --extra dev_extra
uv run python tests/lake/fuzz_example_atheris.py
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

## Experimento de desempenho do workflow

O experimento de [cache dos arquivos públicos](evidencias/ci-cache-fontes/README.md)
antecipa os testes do painel e do preparo das fontes, restaura somente ZIPs e mede
o preparo antes de iniciar a integração. Carga, CDC, dbt, auditoria e painel continuam
sendo executados sobre banco novo. A comparação remota registrou
16 min 14 s sem reaproveitamento e 5 min 29 s com cache; o relatório documenta
as revisões, os hashes, os testes mantidos e os limites dessa amostra.

Isso não substitui o workflow de suíte geral solicitado na #99. O cache e seu
bind em `/dados/bruto` são habilitados apenas pelo override `docker-compose.ci.yml`.
