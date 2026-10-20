# AI-USAGE.md — Squad EduInfra

Registro de uso de assistentes e agentes de IA no Projeto Integrado.

Este arquivo cumpre a [Política de Uso de IA](https://unb-bd2.github.io/Disciplina/uso-de-ia/)
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
