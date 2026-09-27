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

### AAAA-MM-DD — [preencher: uso de IA na modelagem/migrações do esquema]

- **Ferramenta:**
- **Onde:** `alembic/versions/000X_*.py`
- **O que foi pedido:**
- **O que foi aproveitado:**
- **Como foi verificado:**
- **Quem revisou:**

> Preencher por quem trabalhou nas migrações (`0001_cadastro_escolar.py` a
> `0004_estado_atual.py`) e no modelo insert-only, se algum assistente foi
> usado na implementação ou na revisão do esquema.

### AAAA-MM-DD — [preencher: uso de IA na carga e no download do Censo]

- **Ferramenta:**
- **Onde:** `src/eduinfra/carga.py`, `src/eduinfra/microdados.py`,
  `src/eduinfra/transformacao.py`
- **O que foi pedido:**
- **O que foi aproveitado:**
- **Como foi verificado:**
- **Quem revisou:**

> Preencher por quem implementou a carga idempotente e o download do pacote do
> INEP.

### AAAA-MM-DD — [preencher: uso de IA no ADR 0002 — cadeia TLS incompleta do INEP]

- **Ferramenta:**
- **Onde:** `src/eduinfra/tls.py`, `docs/adr/0002-cadeia-tls-incompleta-do-inep.md`
- **O que foi pedido:**
- **O que foi aproveitado:**
- **Como foi verificado:**
- **Quem revisou:**

> Preencher por quem investigou e resolveu o problema de certificado do INEP.

### AAAA-MM-DD — [preencher: uso de IA na caracterização da carga de trabalho]

- **Ferramenta:**
- **Onde:** `src/eduinfra/caracterizacao.py`
- **O que foi pedido:**
- **O que foi aproveitado:**
- **Como foi verificado:**
- **Quem revisou:**
