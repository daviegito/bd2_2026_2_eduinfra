# Fontes, equipe e entregas

O EduInfra AI investiga a relação entre infraestrutura escolar e desempenho
educacional. A proposta é apoiar gestores e pesquisadores com dados
estruturados e, futuramente, consultas em linguagem natural.

## Fontes de dados

| Fonte | Papel | Situação até a E2 |
| --- | --- | --- |
| [Censo Escolar 2024 — INEP](https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/microdados/censo-escolar) | Cadastro de escolas, infraestrutura e linha de base das vistorias | Integrado |
| [Microdados do ENEM — INEP](https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/microdados/enem) | Desempenho educacional | Integração prevista |
| INSE — INEP | Contexto socioeconômico | Integração prevista |
| API do IBGE | Enriquecimento territorial e demográfico | Integração prevista |
| Dicionários do INEP | Interpretação dos campos e metadados | Uso previsto na camada semântica |
| Vistorias | Novas observações sobre as escolas | Origem e CDC implementados; aplicativo previsto |

A base carregada usa escolas em atividade no Censo 2024: 181.065 escolas,
5.570 municípios e 3.078.105 itens de vistoria. Os números e o ambiente da
medição estão na [caracterização](caracterizacao.md).

O técnico é identificado na origem transacional. Os caminhos analíticos de
eventos não exportam sua identificação; detalhes no [modelo da origem](modelo-origem.md).

## Entregas

| Entrega | Escopo | Situação no repositório |
| --- | --- | --- |
| E1 | Origem transacional e carga pública reprodutível | Implementada |
| E2 | Lote idempotente, CDC e armazenamento analítico aberto | Implementada; ADR 0004 proposto |
| E3 | Modelagem analítica, testes de dados e orquestração | Prevista |
| E4 | Consumo, governança e plataforma integrada | Prevista |

Os prazos devem ser consultados no
[cronograma da disciplina](https://unb-bd2.github.io/Disciplina/cronograma/).
O estado acima descreve a implementação, não a avaliação ou aprovação das entregas.

## Equipe

| Integrante | GitHub |
| --- | --- |
| Artur Mendonça Arruda | [@ArtyMend07](https://github.com/ArtyMend07) |
| Davi Coelho | [@daviegito](https://github.com/daviegito) |
| Gabriel Lopes de Amorim | [@BrzGab](https://github.com/BrzGab) |
| Lucas Mendonça Arruda | [@lucasarruda9](https://github.com/lucasarruda9) |
| Marcos Vinícius | [@MarcosViniciusG](https://github.com/MarcosViniciusG) |
| Pedro Sanchez | [@PedroDev-sketch](https://github.com/PedroDev-sketch) |
| Pedro Vargas | [@Pedrovargas10](https://github.com/Pedrovargas10) |

## Registros do projeto

- [Decisões de arquitetura](adr/README.md): alternativas, medições e consequências.
- [Diário de bordo](diario/diario-de-bordo.md): registro histórico das semanas 1 a 10.
- [Uso de IA](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/AI-USAGE.md): registro mantido no repositório.
- [Código e licença GPL-3.0](https://github.com/daviegito/bd2_2026_2_eduinfra).
