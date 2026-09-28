# Diário de bordo — Squad EduInfra

Registro semanal curto. Três perguntas, uma entrada por Semana. É insumo das
retrospectivas e da defesa final, e é a evidência que sustenta o componente de
processo da avaliação de Entrega.


## Semana 1 — Formação da Squad (11–17/08)

**O que foi medido**

Nada técnico ainda. Único número relevante: o grupo abriu com 4 pessoas (Davi, Marcos Vinícius,
Pedro Vargas, Pedro T.), com o plano do WhatsApp permitindo até 6–7
integrantes.

**O que surpreendeu**

Não existia ainda um grupo da turma inteira para tirar dúvidas — a Squad
precisou se organizar sem esse canal nas primeiras semanas.

**O que foi decidido**

Grupo criado e renomeado para "Squad - BD2". Decidido aguardar novos membros
via link de convite, em vez de recrutar ativamente.

**Pendências para a Semana seguinte**

- [x] Ver se mais alguém entra pelo link antes de fechar os 4 membros iniciais


## Semana 2 — Primeiras ideias de domínio (18–24/08)

**O que foi medido**

Nada técnico. A Squad cresceu de 4 para 7 nomes candidatos após divulgar o
link numa planilha compartilhada entre grupos da disciplina.

**O que surpreendeu**

Duas ideias de domínio surgiram quase ao mesmo tempo, por caminhos
independentes: uma pensada pelo Pedro Vargas (estágio × tempo de formação) e
outra sugerida por um assistente de IA a pedido do Marcos (explorador de
trajetórias acadêmicas). A entrada de 3 pessoas novas na Squad via planilha
também não fazia parte do plano original de composição do grupo.

**O que foi decidido**

Nenhuma decisão de domínio fechada ainda — as duas ideias ficaram em aberto
para amadurecer na Semana seguinte, junto com a decisão de como abordar os 3
novos nomes da planilha.

**Pendências para a Semana seguinte**

- [ ] Decidir se e como convidar os 3 nomes que apareceram na planilha
- [ ] Amadurecer as duas ideias de domínio


## Semana 3 — Domínio escolhido: ENEM × Infraestrutura Escolar (25–31/08)

**O que foi medido**

Nenhuma métrica de dado ainda — a Squad estava na fase de definição de
domínio, não de implementação.

**O que surpreendeu**

Um texto de proposta para o domínio ENEM × Infraestrutura trouxe uma
afirmação não verificada — de que o INEP teria tornado "impossível"
relacionar as duas bases do ENEM 2025 por proteção de dados. A Squad discutiu
e concluiu que provavelmente se tratava de uma alucinação do modelo de IA
usado para auxiliar na redação da proposta, e decidiu não assumir essa
premissa sem confirmação (ver `AI-USAGE.md`, entrada de 25/08).

**O que foi decidido**

Gabriel, Artur e Lucas entraram na Squad via planilha. Em enquete, o domínio
ENEM × Infraestrutura Escolar venceu o de trajetórias acadêmicas por 5 votos a
0. Planilha de acompanhamento da disciplina preenchida com a escolha.

**Pendências para a Semana seguinte**

- [ ] Confirmar se a afirmação sobre a impossibilidade de relacionar as bases
      do ENEM 2025 procede, antes de assumi-la como restrição real do projeto
- [ ] Entender o que a professora espera do formato final da entrega (não
      ficou claro se o projeto vira uma "plataforma")


## Semana 4 — Organização de ferramentas (01–07/09)

**O que foi medido**

Nada técnico. A Squad organizou a logística: o When2Meet respondido pelo
grupo apontou terça-feira a partir das 21h como horário comum a todos.

**O que surpreendeu**

O primeiro Encontro previsto para a Semana seguinte (07/09) era feriado, algo
que só foi percebido a tempo graças ao aviso do Gabriel.

**O que foi decidido**

Discord da Squad criado. Planilha de acompanhamento consolidada com uma cópia
por pessoa e uma versão final. Prazo para todos preencherem sua cópia fixado
em terça, 09/09, por enquete, com votação unânime contra a alternativa de
sexta 11/09.

**Pendências para a Semana seguinte**

- [ ] Cada integrante preencher sua cópia da planilha até 09/09
- [ ] Marcar a primeira reunião da Squad pelo When2Meet


## Semana 5 — Primeira reunião, repositório e primeiro obstáculo de dados (08–14/09)

**O que foi medido**

Nenhuma métrica do banco ainda, mas surgiu o primeiro obstáculo real de
dados: o pacote do ENEM 2025 baixado trazia apenas `Participantes.csv` e
`Itens_Prova.csv`, sem base de resultados — identificado pelo Lucas em 07/09
e ainda sem solução em 08/09.

**O que surpreendeu**

O primeiro contato com o material de apoio da disciplina já expôs uma
fragilidade de acesso: parte do grupo não conseguiu abrir o PDF de Object
Storages disponibilizado pelo professor, e o arquivo só circulou depois de
passar por mais de um aplicativo até chegar a todos. É o mesmo tipo de
obstáculo de acesso a dado público que o domínio escolhido pela Squad se
propõe a investigar, só que, desta vez, na própria disciplina. A logística
de reunião também exigiu ajuste de última hora: dois When2Meets
independentes, um da Squad e outro de outra disciplina cursada por parte do
grupo, apontaram o mesmo horário, e o conflito só foi resolvido na própria
noite da reunião.

**O que foi decidido**

Repositório GitHub do projeto criado (`bd2_2026_2_eduinfra`), com branches
`develop` e `e1`. Todos os nicks do GitHub coletados e adicionados como
colaboradores. Página da Entrega 1 (`/projeto/e1/`) localizada — vale 10% da
nota, prazo original 22/09. Decidido continuar com o ENEM 2024 como base
provisória, já que o 2025 não tem resultados disponíveis.

**Pendências para a Semana seguinte**

- [ ] Resolver o acesso ao material de Object Storage para quem ainda não
      conseguiu
- [ ] Confirmar se dá para usar o ENEM 2024 sem prejuízo do projeto


## Semana 6 — Validação do domínio e mudança de prazo (15–21/09)

**O que foi medido**

Nenhuma métrica de dado ainda.

**O que surpreendeu**

O receio da Semana anterior — ter que trocar de base por causa da série
interrompida do ENEM — se dissolveu ao reler o material da disciplina:
descontinuidade e inconsistência de dado público é o objeto da matéria, não
um obstáculo a ser evitado. "Série interrompida" era exatamente a situação da
Squad.

**O que foi decidido**

Mantida a base do ENEM/Censo Escolar, sem trocar de domínio. Prazo da E1
adiado de 22/09 para 28/09 pela professora, repassado pelo Davi no Discord.
Pedro Vargas consolidou as planilhas individuais da Squad em uma só. Da fala
da professora em sala, Artur trouxe três direcionamentos: não usar
orquestrador do tipo Airflow neste projeto específico, definir a arquitetura
antes de avançar, e considerar GitHub Pages como alternativa ao Power BI para
disponibilização — detalhe que ninguém conseguiu esclarecer melhor depois.

**Pendências para a Semana seguinte**

- [ ] Definir a arquitetura da E1 (origem transacional) a partir do que foi
      discutido em sala
- [ ] Esclarecer com a professora ou monitoria o que ela quis dizer com
      GitHub Pages no lugar de Power BI


## Semana 7 — Implementação da origem transacional (22–28/09)

**O que foi medido**

Execução do comando `caracterizar` a partir de um clone limpo, em 26/09/2026:

| Tabela | Linhas |
| --- | ---: |
| `municipio` | 5.570 |
| `escola` | 181.065 |
| `item_infraestrutura` | 17 |
| `tecnico` | 1 |
| `vistoria` | 181.065 |
| `vistoria_item` | 3.078.105 |

Banco ocupando 445 MB (369 MB apenas em `vistoria_item`). Leitura do estado
atual de uma escola medida em menos de 1 ms com estatísticas atualizadas;
escrita esperada estimada em ~500 vistorias/dia, ainda não medida em
produção.

**O que surpreendeu**

Dois pontos não estavam previstos no planejamento inicial da E1. Primeiro, a
cadeia de certificados TLS do INEP se mostrou incompleta, o que impedia o
download direto dos microdados até ser resolvido — episódio registrado no
ADR 0002, e que exigiu completar a cadeia via AIA em vez da alternativa mais
simples de desabilitar a verificação de certificado. Segundo, o custo de
armazenamento da modelagem insert-only escolhida no ADR 0001 se revelou maior
do que o antecipado: a tabela de itens de vistoria já responde por cerca de
83% do tamanho do banco populado, e cresce em torno de 3 milhões de linhas a
cada novo ciclo de carga — um custo que a Squad decidiu pagar conscientemente
em troca de nunca perder o histórico de uma escola.

**O que foi decidido**

Modelagem da origem definida como insert-only (vistoria e vistoria_item nunca
sofrem UPDATE/DELETE, apenas INSERT), registrada no
[ADR 0001](../adr/0001-modelagem-insert-only-do-sistema-de-origem.md). Decisão
de completar a cadeia TLS do INEP via AIA em vez de relaxar a verificação de
certificado, registrada no
[ADR 0002](../adr/0002-cadeia-tls-incompleta-do-inep.md). Esquema versionado
em 4 migrações Alembic, carga automatizada e idempotente via Docker Compose.

**Pendências para a Semana seguinte**

- [ ] Mergear a PR que adequa o ADR 0001 ao template da disciplina e adiciona
      o índice de ADRs
- [ ] Definir o plano de ingestão para a E2 (Semana 10)
