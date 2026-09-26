# ADR 0001 — Adotar esquema insert-only com carimbo de tempo do evento na origem

| Campo | Valor |
| --- | --- |
| Status | Aceito |
| Data | 16 de setembro de 2026 |
| Decidido por | Squad EduInfra |
| Entrega | E1 |

## Contexto

A secretaria de educação vistoria a infraestrutura das escolas periodicamente. O
aplicativo de vistorias é o sistema de origem do EduInfra AI: é dele que sai o
dado transacional que alimenta a camada analítica e, mais adiante, o assistente
RAG.

A pergunta de gestão que a plataforma existe para responder é temporal: *a
infraestrutura de uma escola se reflete no desempenho dos seus alunos no ENEM?*
A forma mais forte dessa pergunta é comparativa no tempo — o desempenho subiu
depois que o laboratório foi construído?

Um esquema CRUD convencional sobrescreveria o registro da escola a cada nova
vistoria. O banco passaria a conhecer apenas o estado presente, e a pergunta
acima deixaria de ter resposta possível, independentemente do que a camada
analítica fizesse depois.

## Requisitos e restrições

- Preservar o histórico completo de cada item de infraestrutura por escola.
- Distinguir quando a realidade mudou de quando o dado entrou no sistema.
- Volume da carga inicial: 181.065 escolas em atividade no Censo Escolar 2024,
  17 itens por escola.
- Escrita transacional pontual pelo aplicativo; leitura analítica retroativa em
  lote pelo pipeline.
- A equipe domina SQL e PostgreSQL. Não há orçamento para serviço gerenciado.

## Padrões de acesso previstos

| Consulta | Origem | Frequência |
| --- | --- | --- |
| Registrar vistoria de uma escola | Aplicativo da secretaria | Contínua, baixa vazão |
| Estado corrente de infraestrutura de uma escola | Aplicativo e painel | Alta |
| Série histórica de um item por escola | Pipeline analítico | Lote, anual |
| Extração completa para a camada analítica | Pipeline de ingestão (E2) | Lote |

## Decisão

Adotar modelo insert-only. Cada mudança gera uma nova linha em
[vistoria](../../alembic/versions/0003_vistoria_insert_only.py), com `ocorrido_em`
registrando quando a mudança aconteceu na escola e `registrado_em` quando o dado
chegou ao banco. `UPDATE`, `DELETE` e `TRUNCATE` sobre as tabelas de evento são
bloqueados por gatilho, não por convenção de aplicação.

## Alternativa 1 — Esquema CRUD com atualização destrutiva

- **Prós:** modelagem trivial; a tabela não cresce; o estado corrente é uma
  leitura direta, sem window function.
- **Contras:** destrói o histórico. O banco só sabe descrever o agora.
- **Por que não foi escolhida:** inviabiliza o cruzamento temporal entre notas do
  ENEM e infraestrutura da época, que é a pergunta central do projeto.

## Alternativa 2 — Insert-only com carimbo de ingestão apenas

- **Prós:** o próprio banco preenche o carimbo com `default now()`, sem depender
  do preenchimento correto pelo técnico em campo.
- **Contras:** confunde a data em que o dado entrou no sistema com a data em que
  a escola de fato ganhou o laboratório. Uma carga histórica feita hoje dataria
  todo o passado como se fosse hoje.
- **Por que não foi escolhida:** a carga de linha de base vem do Censo Escolar
  2024, cuja data de referência é 29/05/2024. Com carimbo de ingestão, toda a
  base nasceria datada da execução do script, e a série temporal seria falsa já
  na primeira carga.

## Evidência

- A carga de linha de base do Censo 2024 produz 181.065 vistorias e 3.078.105
  linhas de item em uma execução de 2 min 51 s, todas datadas de 29/05/2024, a
  data de referência do Censo, e não da execução do script. Os números medidos
  estão em [caracterizacao-carga.md](../caracterizacao-carga.md).
- O índice único parcial sobre `(co_entidade, ocorrido_em)` para vistorias de
  origem `censo_escolar` torna a carga idempotente: rodando a carga duas vezes
  seguidas, a segunda execução inseriu zero linhas.
- A leitura do estado corrente de uma escola pela view `escola_estado_atual`
  custou de 0,13 ms a 0,24 ms com a base populada, contra os 300 ms tolerados na
  tela do técnico. O custo do insert-only, neste volume, é irrelevante para o
  caminho transacional.
- Os gatilhos de bloqueio têm teste automatizado em
  [test_insert_only.py](../../tests/test_insert_only.py).

## Consequências positivas

- Histórico de infraestrutura sai de graça para a camada analítica, sem CDC
  adicional na origem.
- A E2 pode ingerir por marca d'água sobre `registrado_em` sem depender de
  gatilho de auditoria.
- Auditoria da secretaria fica possível: nenhuma vistoria some do registro.

## Consequências negativas

- O volume cresce a cada ciclo de vistoria, sem compensação por atualização.
- Descobrir o estado corrente exige `DISTINCT ON` sobre o evento mais recente de
  cada par escola-item, encapsulado na view `escola_estado_atual`.
- Correção de erro de digitação de uma vistoria exige registrar uma retificação,
  não editar a linha errada.

## Riscos assumidos

- Crescimento de armazenamento que force particionamento por ano antes do fim do
  projeto.
- Degradação da leitura do estado corrente conforme o número de vistorias por
  escola aumenta.

## Como e quando revisitar

Esta decisão será reaberta se qualquer um destes sinais aparecer:

- a leitura de `escola_estado_atual` para uma escola passar de 200 ms, caso em
  que a view vira materializada com refresh ao fim de cada carga;
- a tabela `vistoria_item` passar de 50 milhões de linhas, caso em que entra
  particionamento por ano do evento;
- o aplicativo da secretaria passar a exigir edição direta de vistoria por
  requisito legal, caso em que o padrão de retificação precisa ser reavaliado.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Registro inicial da decisão de modelagem da origem | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 16 de setembro de 2026 |
| 1.1 | Evidência atualizada com os números medidos da carga do Censo 2024 | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 22 de setembro de 2026 |
