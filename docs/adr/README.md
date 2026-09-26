# Decisões de arquitetura

Registro das decisões da Squad EduInfra, no formato Nygard e seguindo o
[Método de Decisão](https://unb-bd2.github.io/Disciplina/adr/) da disciplina.
Para escrever um ADR novo, copie o [template](0000-template.md) e salve como
`NNNN-titulo-em-kebab-case.md`.

## Portfólio avaliado

Os 5 ADRs obrigatórios da disciplina, um por tema.

| ADR | Decisão | Entrega | Status | Issue |
| --- | --- | --- | --- | --- |
| [0001](0001-modelagem-insert-only-do-sistema-de-origem.md) | Esquema insert-only com carimbo de tempo do evento na origem | E1 | Aceito | #3 |
| 0003 | Armazenamento analítico em formato aberto | Tema livre | A escrever | #4 |
| 0004 | Ingestão em lote e captura de mudanças | E2 | A escrever | #5 |
| 0005 | Transformação, testes de dados e orquestração | E3 | A escrever | #6 |
| 0006 | Consumo (painel e camada semântica) e ETL reverso | E4 | A escrever | #7 |

## Registros complementares

Decisões de implementação que merecem registro, mas não são escolha de
arquitetura da camada de dados e ficam fora do portfólio avaliado.

| ADR | Decisão | Status |
| --- | --- | --- |
| [0002](0002-cadeia-tls-incompleta-do-inep.md) | Completar a cadeia TLS do INEP por AIA em vez de relaxar a verificação | Aceito |

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Índice dos ADRs e template da disciplina | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 26 de setembro de 2026 |
