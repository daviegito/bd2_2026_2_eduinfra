# Decisões de arquitetura

Registro das decisões da Squad EduInfra, no formato Nygard e seguindo o
[Método de Decisão](https://unb-bd2.github.io/PlanoEnsino/adr/) da disciplina.
Para escrever um ADR novo, copie o [template](0000-template.md) e salve como
`NNNN-titulo-em-kebab-case.md`.

## Portfólio avaliado

Os 5 ADRs obrigatórios da disciplina, um por tema.

| ADR | Decisão | Entrega | Status | Issue |
| --- | --- | --- | --- | --- |
| [0001](0001-modelagem-insert-only-do-sistema-de-origem.md) | Esquema insert-only com carimbo de tempo do evento na origem | E1 | Aceito | #3 |
| 0003 | Armazenamento analítico em formato aberto | Tema livre | A escrever | #4 |
| [0004](0004-ingestao.md) | Combinar lote idempotente do Censo com CDC pelo slot lógico do PostgreSQL | E2 | Aceito | #5 |
| [0005](0005-ferramenta-de-transformacao.md) | Usar o dbt para as transformações da Gold | E3 | Proposto | #6 |
| 0006 | Consumo (painel e camada semântica) e ETL reverso | E4 | A escrever | #7 |

## Registros complementares

Decisões de implementação que merecem registro, mas não são escolha de
arquitetura da camada de dados e ficam fora do portfólio avaliado.

| ADR | Decisão | Status |
| --- | --- | --- |
| [0002](0002-cadeia-tls-incompleta-do-inep.md) | Completar a cadeia TLS do INEP por AIA em vez de relaxar a verificação | Aceito |
| [0007](0007-telemetria-e-fila-de-erros-do-cdc.md) | Registrar telemetria por transação no consumidor do CDC e isolar linhas ruins numa fila de erros | Aceito |
| [0008](0008-fonte-canonica-de-eventos.md) | Adotar a tabela Delta `eventos_vistoria` como fonte única de eventos, completada pelo CDC e pelas janelas | Proposto |

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Índice dos ADRs e template da disciplina | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 26 de setembro de 2026 |
| 1.1 | Inclui o ADR 0007 nos registros complementares | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 31 de outubro de 2026 |
| 1.2 | Inclui o ADR 0008 nos registros complementares | Squad EduInfra |  | 8 de novembro de 2026 |
| 1.3 | Marca o ADR 0007 como aceito | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 9 de novembro de 2026 |
