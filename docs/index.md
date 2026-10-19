# EduInfra AI

Infraestrutura escolar e desempenho educacional — projeto de Sistemas de
Banco de Dados 2, UnB/FCTE, 2026/2.

**Pergunta de gestão:** como a infraestrutura das escolas se relaciona com o
desempenho dos estudantes no ENEM?

O projeto combina um histórico de vistorias escolares com uma camada analítica.
O objetivo futuro é permitir que gestores e pesquisadores explorem esses dados
por painéis e por um assistente em linguagem natural.

## Estado atual — E2

| Implementado | Próximas entregas |
| --- | --- |
| Origem PostgreSQL com vistorias insert-only | Integração de ENEM, INSE e IBGE |
| Carga do Censo com manifesto e reexecução idempotente | Modelagem e indicadores na Gold |
| Captura de novas vistorias pelo CDC | Painéis e assistente RAG |
| Parquet, tabelas Delta e consultas DuckDB | Busca vetorial e cache |
| Tratamento de eventos atrasados e medições | Integração dos caminhos analíticos |

A ingestão usa lote anual para o Censo e CDC para as novas vistorias.
Os destinos analíticos ainda coexistem; não formam uma tabela única integrada.
O [ADR 0004](adr/0004-ingestao.md) registra as garantias e limitações e permanece
com status **proposto**. O diário de bordo está atualizado até a semana 10.

## Explore a documentação

<div class="grid cards" markdown="1">

- **Entenda o projeto**

    [Arquitetura](arquitetura.md) e [modelo da origem](modelo-origem.md):
    o caminho dos dados e as responsabilidades de cada componente.

- **Execute e demonstre**

    [Guia de execução](execucao.md) e [testes](testes.md):
    preparação do ambiente, comandos e verificações.

- **Confira as evidências da E2**

    [CDC](cdc-e-semantica-de-entrega.md), [compressão](serializacao-compressao.md),
    [dados atrasados](dado-atrasado.md) e [camada analítica](camada-analitica.md).

- **Acompanhe as decisões**

    [ADRs](adr/README.md), [fontes, equipe e entregas](projeto.md)
    e [diário de bordo](diario/diario-de-bordo.md).

</div>
