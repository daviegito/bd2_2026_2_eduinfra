# AI-USAGE.md — Squad EduInfra

Registro de uso de assistentes e agentes de IA no Projeto Integrado.

Este arquivo cumpre a [Política de Uso de IA](https://unb-bd2.github.io/PlanoEnsino/uso-de-ia/)
da disciplina. Ele não é confissão nem formalidade: é o mesmo tipo de registro
que um ADR faz para decisões de arquitetura.

**Duas regras de forma.** Escreva **no momento do uso**, não na véspera da
Entrega — registro reconstruído de memória sai impreciso, e imprecisão aqui é o
que a política pune. E versione junto com o código: uma entrada por commit
relevante é melhor que um resumo mensal.

**Não precisa registrar** autocompletar de editor, correção ortográfica ou
tradução. Registre o que produziu artefato ou mudou uma decisão.

---

## Entradas

### 2026-08-25 — Geração da proposta de domínio (ENEM × Infraestrutura Escolar)

- **Ferramenta:** Claude (assistente conversacional)
- **Onde:** definição do domínio, persona, perguntas de pesquisa e fontes de
  dados do projeto — texto depois reaproveitado no README e na escolha final
  de domínio pela enquete da Squad
- **O que foi pedido:** gerar uma descrição estruturada do problema, da
  persona (gestor regional de educação), das perguntas de pesquisa e das
  fontes de dados (microdados do ENEM e do Censo Escolar do INEP) para a ideia
  de projeto sobre infraestrutura escolar e desempenho no ENEM
- **O que foi aproveitado:** a estrutura do domínio, a persona, as perguntas
  de pesquisa e o mapeamento das fontes de dados (ENEM 2025/2024 + Censo
  Escolar, chaves `CO_ESCOLA`/`CO_ENTIDADE`). **Não foi aproveitada** — e ficou
  marcada como suspeita — a afirmação de que o INEP teria tornado "impossível"
  relacionar as duas bases do ENEM 2025 por proteção de dados
- **Como foi verificado:** a afirmação suspeita foi discutida no grupo
  (24–25/08); Davi questionou o trecho e Gabriel concordou que provavelmente
  era alucinação do modelo. A Squad optou por **não assumir essa premissa**
  sem confirmação técnica direta na documentação oficial do INEP antes de
  usá-la em qualquer decisão de projeto
- **Quem revisou:** Davi e Gabriel, em discussão no grupo da Squad
- **Atualização (2026-09-28):** conferida na documentação oficial, a afirmação
  procede. O Leia-me dos microdados do ENEM 2025 diz que "os arquivos
  PARTICIPANTES_2025.csv e RESULTADOS_2025.csv não possuem chave de ligação em
  comum", para que a escola de conclusão não permita identificar o
  participante. Ou seja, não é possível ligar o questionário socioeconômico de
  um aluno à nota e à escola dele. Não foi alucinação; a cautela da Squad em
  não assumir a premissa sem fonte é que estava certa.

### 2026-09-16 — Consolidação das planilhas da Squad e decisões de arquitetura da E1

- **Ferramenta:** Claude
- **Onde:** Planilhas de acompanhamento da disciplina, consolidação das propostas individuais da Squad em versão unificada (`Planilha_BD2_V3_Arquitetura_Escola_2024.xlsx`) e documentação de justificativas arquiteturais
- **O que foi pedido:**
  1. Análise e preenchimento inicial da planilha individual com base no problema do domínio;
  2. Pesquisa e validação das fontes de dados públicas do INEP (ENEM, Censo Escolar, INSE) e justificativas técnicas para formatos e motores (Parquet, PostgreSQL, DuckDB);
  3. Extração, análise cruzada e síntese de 7 planilhas individuais distintas dos integrantes da Squad, unificando temas comuns e melhores argumentos em uma planilha consolidada para o grupo;
  4. Adequação da arquitetura aos direcionamentos trazidos da aula (3 estágios de dados / S3, camada semântica para IA, enriquecimento geográfico com API do IBGE e preservação da fonte transacional OLTP/insert-only exigida na E1);
  5. Investigação sobre o impacto da LGPD na supressão do `CO_ESCOLA` entre 2018 e 2023, avaliando o trade-off entre agregar por município ou manter o grão de escola restringindo a análise à edição de 2024;
- **O que foi aproveitado:**
  - A síntese das propostas em uma planilha final única no template oficial da disciplina, eliminando redundâncias e alinhando o grupo em uma arquitetura consistente;
  - A identificação da restrição do `CO_ESCOLA` nos microdados do INEP e a decisão arquitetural documentada no ADR de manter o grão da escola em 2024 e enriquecer com o IBGE;
  - O diagnóstico de conformidade com o checklist da E1, evitando a remoção indevida da fonte OLTP (sistema de vistorias) que causaria perda de pontos na entrega;
  - O roteiro de defesa e os argumentos técnicos para justificar a escolha de cada engine, formato e modelagem.
- **Como foi verificado:**
  - Conferência direta com as regras e checklist da E1 no site da disciplina (`unb-bd2.github.io/PlanoEnsino/projeto/e1/`), identificando a necessidade de preservar a origem transacional que quase havia sido descartada;
  - Verificação na documentação e portais do INEP sobre a disponibilidade dos microdados e as mudanças de esquema pós-LGPD;
  - Inspeção e validação do arquivo `.xlsx` gerado para conferir se todas as abas, células e fórmulas de validação atingiam o mínimo exigido pelo template;
- **Quem revisou:** Pedro Vargas

### 2026-09-23 — Esquema insert-only, migrações Alembic e ADR 0001

- **Ferramenta:** Claude (Claude Code, com acesso ao ambiente de execução)
- **Onde:** `alembic/` (`env.py` e as revisões `0001` a `0004`), `tests/test_insert_only.py`, `src/eduinfra/comparacao_modelagem.py` e `docs/adr/0001-modelagem-insert-only-do-sistema-de-origem.md`
- **O que foi pedido:** Artur definiu a direção: origem transacional insert-only para a E1, `uv` no lugar do `pip` e Alembic no lugar de um executor de arquivos `.sql`. A primeira versão do esquema saiu em `.sql` e foi refeita em Alembic por essa decisão. Ele também exigiu que o que fosse versionado tivesse sido conferido contra o banco real
- **O que foi aproveitado:** o esquema (cadastro escolar, catálogo de infraestrutura, vistoria e item insert-only, visão do estado atual), as quatro revisões, os testes de insert-only e a comparação reproduzível entre CRUD e insert-only usada no ADR 0001. A escrita final do ADR e a adequação ao template da disciplina passaram pela revisão do PR #13
- **Como foi verificado:** migrações aplicadas, revertidas e reaplicadas num PostgreSQL 16 em Docker; testes de integridade rodando contra esse banco; subida do zero com `docker compose up`
- **Quem revisou:** Lucas Mendonça Arruda (PR #11); Davi e Gabriel (PR #13)

### 2026-09-24 — Carga reprodutível do Censo Escolar 2024

- **Ferramenta:** Claude (Claude Code, com acesso ao ambiente de execução)
- **Onde:** `src/eduinfra/carga.py`, `src/eduinfra/microdados.py`, `src/eduinfra/transformacao.py`, `src/eduinfra/cli.py`, `src/eduinfra/configuracao.py`, `Dockerfile`, `docker-compose.yml` e `tests/test_transformacao.py`
- **O que foi pedido:** um comando que baixe o pacote público do INEP e popule o banco sem passo manual, com volume real e idempotência, como a E1 exige. Artur fixou as restrições: configuração só por variável de ambiente, nenhum dado bruto no repositório e `uv` para as dependências
- **O que foi aproveitado:** o download, a transformação do CSV nas tabelas da origem e a carga idempotente, que produz 181.065 escolas e 3.078.105 itens de vistoria
- **Como foi verificado:** `docker compose up` do zero até o fim da carga com os dados reais do INEP e `pytest` da transformação
- **Quem revisou:** Lucas Mendonça Arruda (PR #11)

### 2026-09-26 — ADR 0002: cadeia TLS incompleta do INEP

- **Ferramenta:** Claude (Claude Code, com acesso ao ambiente de execução)
- **Onde:** `src/eduinfra/tls.py` e `docs/adr/0002-cadeia-tls-incompleta-do-inep.md`
- **O que foi pedido:** resolver a falha de certificado no download do INEP sem desligar a verificação. Artur ficou com a decisão de manter a verificação ativa
- **O que foi aproveitado:** a solução que completa a cadeia pelo endereço de emissão do certificado (AIA) e o texto do ADR, que registra a alternativa descartada
- **Como foi verificado:** o download real do pacote do INEP, com a verificação de certificado ligada, dentro da carga completa
- **Quem revisou:** Gabriel (PR #12)

### 2026-09-27 — Documentação de processo da E1 (diário de bordo e AI-USAGE.md)

- **Ferramenta:** Claude (assistente conversacional)
- **Onde:** `docs/diario/semana-01-a-07.md`, `AI-USAGE.md` e a descrição da PR
  do diário de bordo
- **O que foi pedido:** levantar quais documentos faltavam pra E1 checando o
  site da disciplina, o repositório e as issues abertas; gerar um rascunho do
  diário de bordo (Semanas 1 a 7) a partir do histórico da Squad no WhatsApp
  e do estado do repositório; auxílio na geração do próprio `AI-USAGE.md`, lendo as
  conversas do grupo para encontrar pontos importantes; e auxílio para escrever a
  descrição da PR do diário
- **O que foi aproveitado:** a identificação dos documentos faltantes (issues
  #8 e #9), a estrutura e parte do conteúdo do diário de bordo, e o
  texto da PR. Duas seções do diário ("O que surpreendeu" das Semanas 5 e 7)
  foram reescritas a pedido, porque a versão à mão ficou fraca ou
  incompleta demais para entrar como estava
- **Como foi verificado:** conferência manual de cada entrega contra o
  histórico real do grupo e o repositório (issues, ADRs, README); edição
  direta do arquivo entre uma geração e outra, corrigindo nome (Marcos) e
  tom de trechos específicos antes de aceitar o conteúdo
- **Quem revisou:** Pedro Teixeira

### 2026-09-28 — Consolidação da caracterização da carga

- **Ferramenta:** Claude (Claude Code, com acesso ao ambiente de execução)
- **Onde:** `docs/caracterizacao.md` e `src/eduinfra/sample_generator.py`
- **O que foi pedido:** Artur pediu a caracterização no formato do passo 1 do Método de Decisão e pediu que ela fosse somada ao que o Lucas já tinha escrito no `docs/caracterizacao.md`, sem apagar o que estava correto
- **O que foi aproveitado:** a consolidação num único `docs/caracterizacao.md` e o gerador de amostra
- **Como foi verificado:** os pontos sobre índices e restrições foram conferidos nas migrações Alembic, e o texto foi comparado com a versão do Lucas antes de unir
- **Quem revisou:** Lucas Mendonça Arruda (PR #20)

### 2026-10-12 — Migração 0005: habilitar a decodificação lógica do WAL (issue #22)

- **Ferramenta:** Perplexity (assistente conversacional)
- **Onde:** `alembic/versions/0005_habilita_cdc_logico.py`
- **O que foi pedido:** ajuda para melhor entender os requisitos pedidos
- **O que foi aproveitado:** a estrutura de outras migrações do alembic
- **Como foi verificado:** testes foram realizados para confirmar se os critérios de aceite foram feitos e um PR foi aberto para que outra pessoa revisasse também.
- **Quem revisou:** Davi

### 2026-10-12 — CDC pelo slot lógico e semântica de entrega (issues #25 e #26)

- **Ferramenta:** Claude (Claude Code, com acesso ao ambiente de execução)
- **Onde:** `src/eduinfra/pgoutput.py`, `cdc.py`, `fluxo_cdc.py`, `eventos.py`, `demonstracao_cdc.py`, `comparacao_ingestao.py`, `vistorias_de_teste.py`, `tests/test_pgoutput.py`, `tests/test_eventos.py`, `tests/test_fluxo_cdc_integracao.py`, o serviço `cdc` do `docker-compose.yml`, `docs/cdc-e-semantica-de-entrega.md` e a seção nova do README (PR #34)
- **O que foi pedido:** Artur escolheu o escopo: as issues #25 e #26, sem o que fosse só da #24 (que já tinha responsável) e sem nada além do necessário para as duas funcionarem. Também definiu as referências nas issues
- **O que foi aproveitado:** o decodificador de mensagens `pgoutput`, o consumidor em duas fases (espiar, gravar no Delta, confirmar o slot), a tabela Delta de eventos com `merge` pela chave `(vistoria_id, item_codigo)`, a demonstração do CDC com falha e reprocessamento, a medição contra a recarga incremental e a documentação. A semântica declarada (no mínimo uma vez, com efeito de exatamente uma) é a que o código e a demonstração mostram
- **Como foi verificado:** migrações 0001 a 0005 do zero num banco vazio, `publicar-analitico`, `demonstrar-cdc` (51 itens na entrega normal, 68 após o consumidor parado, 34 reentregues e 0 inseridos após a falha, 17 e depois 0 no reprocessamento) e 40 testes passando. Ficaram sem verificação o healthcheck do serviço `cdc` no Compose e a latência ponta a ponta, como registrado no PR
- **Quem revisou:** Gabriel (PR #34)

### 2026-10-12 — Serialização e compressão da camada Silver (issue #28)

- **Ferramenta:** Claude (Claude Code, com acesso ao ambiente de execução), como
  assistente de implementação e de escrita
- **Onde:** `src/eduinfra/comparacao_compressao.py`, `tests/test_comparacao_compressao.py`,
  o comando `comparar-compressao`, a constante `COMPRESSAO_PARQUET` em
  `transformacao.py` e `docs/serializacao-compressao.md` (PR #31)
- **O que foi pedido:** apoio para implementar a issue #28, no escopo definido por
  Gabriel: comparar CSV e Parquet (sem compressão, snappy e zstd) sobre a Silver real,
  a partir do padrão de acesso analítico
- **O que foi aproveitado:** sugestões e trechos de código para o comando de medição e
  as consultas típicas, a ideia de intercalar as leituras entre formatos para reduzir o
  ruído da máquina e a redação inicial do documento, revisados e ajustados por Gabriel
- **Como foi verificado:** Gabriel executou `comparar-compressao --repeticoes 15` sobre
  o Censo 2024 (3.078.105 itens), conferiu que as respostas eram idênticas em todos os
  formatos e que os testes do módulo passavam
- **Quem revisou:** Davi (PR #31)

### 2026-10-12 — Dado atrasado e reprocessamento de janela (issue #27)

- **Ferramenta:** Claude (Claude Code, com acesso ao ambiente de execução), como
  assistente de implementação e de escrita
- **Onde:** `src/eduinfra/janelas.py`, `src/eduinfra/demonstracao_atraso.py`, os comandos
  `processar-janelas`, `reprocessar-janela` e `demonstrar-atraso`, `tests/test_janelas.py`,
  `tests/test_janelas_integracao.py` e `docs/dado-atrasado.md` (PR #32)
- **O que foi pedido:** apoio para implementar a issue #27, no escopo definido por
  Gabriel: janelas pelo mês de `ocorrido_em`, tolerância a atraso e reprocessamento
  idempotente de uma janela
- **O que foi aproveitado:** sugestões de desenho discutidas e aprovadas por Gabriel
  (janela mensal, tolerância de 7 dias, reabrir a janela em vez de descartar o atraso),
  trechos de código, testes e a redação inicial do documento, revisados e ajustados
  por ele
- **Como foi verificado:** Gabriel rodou `demonstrar-atraso` contra o banco com o
  Censo 2024 e `reprocessar-janela --mes 2024-05` duas vezes, com a mesma impressão
  (7,9 s e 8,1 s); 11 testes de unidade e 5 de integração passando. A primeira versão
  estourou a memória nessa janela, o que levou ao limite de 1 GB no DuckDB. A mensagem
  de erro dos testes de integração foi corrigida depois da revisão do Davi
- **Quem revisou:** Davi (PR #32)

### 2026-10-13 — Ingestão em lote idempotente (issue #23)

- **Ferramenta:** Claude (assistente conversacional com acesso a ambiente de execução)
- **Onde:** `src/eduinfra/ingestao.py`, `alembic/versions/0006_manifesto_ingestao.py`,
  `src/eduinfra/cli.py`, `src/eduinfra/transformacao.py`, `src/eduinfra/carga.py`,
  `tests/test_ingestao.py` e a seção nova do README
- **O que foi pedido:** revisar e auxília no código relevante para a issue #23 sobre a `main` atual, com testes,
  rodando contra um PostgreSQL 16 local, e depois rebasear o trabalho quando as
  PRs de WAL, CDC, compressão e dado atrasado entraram na `main`
- **O que foi aproveitado:** o código e os testes. Decisões de projeto sugeridas
  e a revisar pela Squad: manifesto gravado na mesma transação da carga, Silver
  particionada por `ano_censo=AAAA` e arquivo corrigido que substitui só o
  Parquet, sem reescrever o OLTP (ADR 0001)
- **Como foi verificado:** `pytest` nos módulos de ingestão, transformação e
  insert-only (17 passando); CLI executada três vezes sobre um CSV sintético
  (carga, repetição ignorada e `--forcar` sem linhas novas); leitura da saída
  pela camada analítica existente. 
- **Quem revisou:** Pedro Teixeira

### 2026-10-18 — Elaboração do ADR 0004

- **Ferramenta:** Codex.
- **Onde:** ADR de ingestão, índice e navegação da documentação.
- **Pedido:** consolidar a decisão de ingestão com base na implementação e nas evidências do repositório.
- **Aproveitado:** rascunho sobre lote idempotente, CDC, alternativas, medições, perdas e gatilhos de revisão.
- **Verificação:** comparação com a issue #5, código e documentação da `main` após o merge da PR #36. Os benchmarks citados são os já registrados pela equipe.
- **Quem revisou:** Artur, na aprovação da PR #37, que conferiu as medições e as constantes contra o código e a documentação.

### 2026-10-19 — Diário de bordo da Semana 10 e revisão do AI-USAGE.md da E2

- **Ferramenta:** Claude (Claude Code, com acesso ao repositório)
- **Onde:** `docs/diario/diario-de-bordo-md` (Semana 10) e `AI-USAGE.md`
- **O que foi pedido:** ler o histórico do git (commits, branches remotas e
  ADR 0004) e sugerir o conteúdo da Semana 10, que ainda era uma cópia da
  Semana 6; depois ajustar as pendências para a preparação da E3, já que a E2
  é entregue hoje. Em seguida, comparar este arquivo com as PRs da E2 e com as
  outras branches para achar entradas faltando
- **O que foi aproveitado:** o texto das quatro seções da Semana 10 e as
  pendências da E3; a identificação de que a entrada do Davi sobre a migração
  0005 só existia na branch `docs/ai-usage` e nunca tinha sido integrada,
  trazida para cá por merge para manter a autoria dele
- **Como foi verificado:** os números da Semana 10 foram conferidos no ADR
  0004 e as falhas corrigidas no commit `dce18df`; a IA apontou que os
  benchmarks citados são de 12/10 (Semana 9), para não registrá-los como
  medição nova. A ausência da entrada do Davi foi confirmada com
  `git branch -a --contains 5e4c802` e buscando o texto em todas as branches.
  Os commits foram feitos à mão, sem a IA
- **Quem revisou:** Marcos Vinícius

### 2026-10-26 — Modelo dimensional da Gold e dimensão de variação lenta (E3-3 e E3-4)

- **Ferramenta:** Claude (assistente conversacional com acesso a ambiente de execução)
- **Onde:** `src/eduinfra/modelo_analitico.py`, `src/eduinfra/cli.py`,
  `tests/test_modelo_analitico.py`, `docs/modelo-analitico.md`, `mkdocs.yml` e README
- **O que foi pedido:** Auxílio no desenho e na implementação das dimensões, da dimensão de
  variação lenta (tipo 2) e dos dois fatos de granularidades diferentes, com testes
  e o comando de medição, a partir do esquema da origem
- **O que foi aproveitado:** o código, os testes e a documentação. Decisões de
  projeto sugeridas e a revisar pela Squad: o histórico da dimensão vem dos eventos
  insert-only e não de um segundo ano do Censo, `nao_informado` não é estado, a
  chave substituta é um hash de (escola, início) e o fato largo usa o perfil
  vigente ao fim do ano
- **Como foi verificado:** `pytest` com PostgreSQL 16 real e banco descartável por
  teste (11 testes novos, 32 com os de ingestão, transformação e insert-only); três
  defeitos reintroduzidos de propósito (tratar `nao_informado` como estado, criar
  versão a cada vistoria, chave instável) e o teste correspondente falhou em cada
  caso, o que levou a reforçar o teste da chave. A construção e a comparação rodaram
  num banco **sintético** com o volume do Censo (181 mil escolas, 3 milhões de
  itens).
- **Quem revisou:** Pedro Teixeira Moriel Sanchez

### 2026-10-31 — Métricas do CDC, telemetria e fila de erros (issue #52, ADR 0007)

- **Ferramenta:** Claude (Claude Code, com acesso ao repositório e ao ambiente Docker local)
- **Onde:** `src/eduinfra/telemetria_cdc.py`, `auditoria_cdc.py`, `fila_de_erros.py`,
  `contrato_cdc.py`, `comparacao_latencia.py`, `fluxo_cdc.py`, `pgoutput.py`, migração 0007,
  testes correspondentes, `docs/metricas-cdc.md` e ADR 0007
- **O que foi pedido:** implementar a lista de oito métricas que Artur escreveu antes da
  implementação (checksum de linhas, contagem por operação, fora de ordem, latência ponta a ponta,
  atraso de captura, atraso de carga, desvio de esquema e fila de erros), avaliando cada uma contra a
  arquitetura. Diante dos testes, Artur decidiu criar a fila de erros em vez de parar e alertar,
  publicar as quatro operações em vez de só `INSERT` e comparar a tabela inteira em vez da amostra que
  a lista previa
- **O que foi aproveitado:** o código, os testes e o rascunho da documentação; o frescor como nona
  métrica, por causa da issue #52, aceito por Artur; e a leitura do fora de ordem em três eixos. Foi
  descartada uma medição de 3 amostras que sugeria cerca de 12 s para o evento atrasado, porque a
  medida por fora marcava o instante antes de atualizar a tabela. Corrigida a medida, as 30 amostras
  deram cerca de 4 s
- **Como foi verificado:** 176 testes (unitários e de integração), com as falhas provocadas em bancos
  descartáveis. A revisão do Davi achou um teste que dependia do intervalo entre dois commits, depois
  corrigido. Duas rodadas de medição nos dados reais, com a latência medida por fora e por dentro.
  A hipótese de que os arquivos pequenos piorariam o merge não se confirmou nesta escala. Os números
  de `docs/metricas-cdc.md` vêm das saídas dos comandos ali documentados, guardadas em
  `docs/evidencias/metricas-cdc/`
  O conflito com o #55 ao trazer a main (`cli.py` e este arquivo) foi resolvido com a IA, juntando os
  comandos dos dois lados, e conferido com a suíte em banco temporário com o Censo 2024 (195 de 196 testes;
  o que falhou é o limite de 0,2 s de `test_transacao_longa_inverte...`, em arquivo que o merge não alterou)
- **Quem revisou:** Davi, no PR #59, com a revisão em andamento

### 2026-11-02 — Frescor e SLA da Gold (issue #52)

- **Ferramenta:** Claude (Claude Code, com acesso ao ambiente de execução), como
  assistente de implementação e de escrita
- **Onde:** `src/eduinfra/frescor.py`, os comandos `medir-frescor` e
  `demonstrar-frescor` e o registro em `construir-gold` na CLI, `tests/test_frescor.py`,
  `tests/test_frescor_integracao.py`, `docs/frescor-e-sla.md`, `docs/testes.md`,
  `mkdocs.yml` e README
- **O que foi pedido:** apoio para implementar a issue #52, no escopo definido por
  Gabriel: medir o frescor da tabela que alimenta o painel, declarar o SLA, registrar
  a métrica, falhar na violação e demonstrá-la
- **O que foi aproveitado:** sugestões discutidas e aprovadas por Gabriel (frescor
  como a espera da vistoria pendente mais antiga, e não o tempo desde a última
  construção; SLA de 25 h; registro fora do schema `gold`), trechos de código, testes
  e a redação inicial do documento, revisados e ajustados por ele
- **Como foi verificado:** `construir-gold`, `demonstrar-frescor` e `medir-frescor`
  rodados contra o banco com o Censo 2024 (violação com código de saída 1 e retorno
  ao normal depois da reconstrução); 10 testes de unidade e 3 de integração
  passando, além da suíte de unidade inteira e dos testes do modelo analítico;
  `mkdocs build --strict` sem erro
- **Quem revisou:** pendente (PR)

### 2026-11-08 — Ingestão do ENEM 2024 e junção com o Censo

- **Ferramenta:** Claude (Claude Code, com acesso ao ambiente de execução), como
  assistente de implementação e de escrita
- **Onde:** `src/eduinfra/enem.py`, `ingestao_enem.py`, `cobertura_enem.py`, a
  migração `0008`, a generalização da publicação da Silver (`transformacao.py`) e do
  download (`microdados.py`), os comandos `ingerir-enem` e `medir-cobertura-enem`,
  `tests/test_enem.py`, `tests/test_ingestao_enem.py`, `docs/enem.md` e README
- **O que foi pedido:** implementar a issue de integração do ENEM: conferir o link
  do pacote, verificar a chave `CO_ESCOLA` × `CO_ENTIDADE`, medir a cobertura,
  agregar por escola com critério declarado e generalizar o manifesto
- **O que foi aproveitado:** código, testes e a redação inicial de `docs/enem.md`,
  a revisar. O link foi conferido com a cadeia TLS do ADR 0002 (o 403 era do portal
  `gov.br`, não do servidor de download). A máscara do `CO_ESCOLA` (prefixo `60`,
  escolas com menos de 10 participantes) foi encontrada pela nota 2 do dicionário
  oficial e conferida no arquivo real
- **Como foi verificado:** transformação e cobertura rodadas sobre o ENEM 2024 e o
  Censo 2024 reais (números em `docs/enem.md`, conferidos contra contagens diretas
  no CSV bruto); testes sem banco passando; `mkdocs build --strict` sem erro. No
  Docker: `tests/test_ingestao_enem.py` e `tests/test_ingestao.py` passando (16
  testes), a migração `0008` testada na ida e na volta em banco descartável, e
  `migrar`, `ingerir`, `ingerir-enem` (a segunda execução foi ignorada pelo
  manifesto) e `medir-cobertura-enem` rodados no Compose, com a mesma cobertura da
  evidência; a cadeia TLS do download foi conferida de dentro do contêiner
- **Quem revisou:** Pedro Vargas, no PR #69, que apontou o teste de migração do CDC
  quebrado pelo rename da 0008 e a colisão da revisão 0008 com o #67. O teste foi
  corrigido com a IA (volta à 0007 antes do stamp, mesma linha do #67) e a suíte
  completa passou com a main integrada
