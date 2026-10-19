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

- [x] Confirmar se a afirmação sobre a impossibilidade de relacionar as bases
      do ENEM 2025 procede, antes de assumi-la como restrição real do projeto
      *(confirmada em 28/09: procede. O Leia-me do ENEM 2025 diz que os
      arquivos de participantes e de resultados "não possuem chave de ligação
      em comum". Ver `AI-USAGE.md`, entrada de 25/08.)*
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

## Semana 8 — Apresentação e feedback (29–05/10)

**O que foi medido**

Nenhuma métrica de dado ainda.

**O que surpreendeu**

Para a apresentação, não sabíamos qual formato seria o melhor para apresentar e nos impressionamos que muitos dos grupos tinham criado GitPages para isso. Aproveitamos a ideia e acabamos criando um para o nosso projeto também.

**O que foi decidido**

Quem iria apresentar o quê na apresentação; o formato dos slides - se PowerPoint ou GitPages;

**Pendências para a Semana seguinte**

- [ ] Consolidar o feedback recebido em issues do GitHub
- [ ] Quem iria fazer o quê no pós-feedback

## Semana 9 — Pós-feedback (06–13/10)

**O que foi medido**

O desenvolvimento mostrou que as principais decisões do banco analítico foram orientadas por medições reais, e não apenas por preferência técnica. A escolha de Parquet com zstd reduziu drasticamente o armazenamento sem penalizar leitura; a partição por ano e UF acelerou consultas filtradas; e o CDC via slot lógico garantiu rastreabilidade e tolerância a falhas, ainda que com custo maior que a recarga incremental no volume testado.

**O que surpreendeu**

Compressão pode reduzir muito o armazenamento sem afetar sensivelmente o tempo de consulta; Dado atrasado pode ser tratado por reprocessamento idempotente, em vez de descarte; Uma alteração pequena no WAL lógico pode ser a base indispensável de todo o fluxo de CDC; CDC não é automaticamente mais rápido que recarga incremental; seu benefício está na captura confiável de mudanças; Muitas gravações pequenas fragmentam a tabela e pioram consultas, mas a compactação corrige isso rapidamente.

**O que foi decidido**

As decisões técnicas convergiram para um pipeline analítico baseado em PostgreSQL como origem, Parquet zstd na Silver, Delta Lake particionado por ano e UF na camada analítica e CDC por slot lógico para captura de mudanças.

Essas escolhas privilegiaram três critérios: reduzir armazenamento sem prejudicar consulta, garantir reprocessamento idempotente diante de dados atrasados e assegurar entrega controlada dos eventos mesmo após falhas do consumidor.

**Pendências para a Semana seguinte**

- [ ] Revisar documentação
- [ ] Resolver pendências
- [ ] Elaborar funcionalidades para a E2

## Semana 10 — Revisão da ingestão e ADR 0004 (14–21/10)

**O que foi medido**

Nenhum benchmark novo. O que se mediu nesta Semana foi o comportamento sob
falha da ingestão em lote: a revisão da PR #36 acrescentou 4 testes
(falha durante a troca da Silver, interrupção antes do commit, retorno ao
arquivo anterior A→B→A e queda entre os dois renames). As medições de captura
da Semana 9 foram consolidadas no
[ADR 0004](../adr/0004-ingestao.md): o slot lógico foi mais rápido só com 1
vistoria (19,7 ms contra 72,1 ms) e mais lento com 5.000 vistorias
(4.890,7 ms contra 1.011,0 ms), com contagens idênticas. Latência de ponta a
ponta e p95 seguem sem medição.

**O que surpreendeu**

A revisão da PR encontrou três falhas que os testes originais não pegavam.
A Silver podia ficar com uma mistura de versões, porque era trocada arquivo
a arquivo. Ela também era publicada antes do commit no banco: o bloco
transacional virava um savepoint, e o commit de fato só vinha depois da
publicação. Por fim, um arquivo que voltava a uma versão anterior
era ignorado, porque o hash era comparado com todo o histórico, não só com a
versão vigente. A ingestão "idempotente" estava correta no caminho feliz,
mas não na falha.

**O que foi decidido**

A partição `ano_censo=AAAA` passa a ser trocada inteira, por rename de
diretório, e só depois do commit do banco e do manifesto. A versão vigente
de cada ano é o hash da última execução do manifesto junto com o marcador
`origem.sha256` da Silver. A decisão de combinar lote idempotente e CDC pelo
slot lógico foi registrada no ADR 0004 (status: proposto), escolhida pelo
requisito da E2 e pela captura por ordem de commit, e não por velocidade.
Também foram definidos gatilhos de revisão: atraso do slot acima de 100 MB,
reprocessamento acima de 1 minuto ou 1 GB, e mais de 5% de eventos fora da
tolerância de 7 dias.

**Pendências para a Semana seguinte**

- [ ] Dividir as tarefas da E3 (camada analítica transformada, testada e
      orquestrada, prazo 03/11) entre a Squad
- [ ] Escrever o ADR 0005 (transformação, testes de dados e orquestração,
      issue #6)
- [ ] Escolher a ferramenta de orquestração sem usar Airflow, conforme a
      orientação da professora registrada na Semana 6
- [ ] Unificar os dois caminhos de atraso (Parquet por janelas e CDC em
      Delta) antes de construir a transformação sobre eles
- [ ] Escrever o ADR 0003 (armazenamento analítico em formato aberto), ainda
      "a escrever"
