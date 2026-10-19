# 0001 — Adotar esquema insert-only com carimbo de tempo do evento na origem

- **Status:** aceito
- **Data:** 16 de setembro de 2026
- **Decisores:** Squad EduInfra

## Contexto

A secretaria de educação vistoria a infraestrutura das escolas periodicamente. O
aplicativo de vistorias é o sistema de origem do EduInfra AI, e é dele que sai o
dado que alimenta a camada analítica e o assistente RAG.

A pergunta de gestão é temporal: *a infraestrutura de uma escola se reflete no
desempenho dos seus alunos no ENEM?* A forma mais forte dela é comparativa no
tempo: o desempenho subiu depois que o laboratório foi construído? Um esquema que
sobrescreve o registro a cada vistoria deixa o banco sabendo apenas o presente, e
a pergunta perde a resposta antes de chegar à camada analítica.

**Carga**, medida na base populada com o Censo Escolar 2024 (detalhes em
[caracterizacao-carga.md](../caracterizacao-carga.md)):

| Dimensão | Valor |
| --- | --- |
| Escolas em atividade | 181.065, com 17 itens de infraestrutura cada |
| Linhas de item na linha de base | 3.078.105 |
| Escrita | carga anual em lote e vistorias pontuais pelo aplicativo, cerca de 500 por dia (estimativa da equipe, declarada na planilha de acompanhamento) |
| Leitura transacional | estado atual de uma escola, por chave, a cada tela do aplicativo |
| Leitura analítica | série histórica e extração completa, em lote, pelo pipeline da E2 |
| Latência tolerada | 300 ms na tela do técnico; segundos para o lote |

**Restrições:** preservar o histórico de cada item por escola; distinguir quando
a realidade mudou de quando o dado entrou no sistema; a equipe domina SQL e
PostgreSQL; não há orçamento para serviço gerenciado.

## Alternativas consideradas

### A. Opção nula: CRUD, uma linha por escola e item, sobrescrita a cada vistoria

É o que um aplicativo de cadastro faz por padrão, e é viável: é a modelagem mais
compacta e a de leitura mais rápida, como a medição confirma. Não foi escolhida
porque cada vistoria apaga o que a anterior dizia. Depois de uma revisita, o banco
não consegue mais responder qual era a infraestrutura da escola na data do ENEM,
e é exatamente essa a junção que a pergunta de gestão exige.

### B. Insert-only com carimbo de ingestão apenas

Guarda o histórico e dispensa o técnico de informar a data, porque o banco
preenche `default now()`. Fisicamente é igual à alternativa C. Não foi escolhida
porque confunde a data em que o dado entrou com a data em que a escola ganhou o
laboratório: a carga de linha de base, cuja referência é 29/05/2024, nasceria
inteira datada do dia em que o script rodou, e a série temporal já começaria
falsa.

### C. Insert-only com carimbo do evento e da ingestão

Cada vistoria é uma nova linha, com `ocorrido_em` (quando a realidade mudou) e
`registrado_em` (quando o dado chegou). Cobra em volume e em custo de leitura do
estado atual.

## Medição

As duas modelagens foram montadas a partir da mesma carga real do Censo 2024, em
um esquema temporário, e submetidas a três ciclos de revisita de 10% das escolas
cada, cerca de 18 mil escolas por ciclo. As revisitas são simuladas, porque o
aplicativo ainda não tem vistorias presenciais: cada uma repete o que o Censo
disse e muda a situação de um item. Leituras medidas pelo tempo de execução no
servidor, sobre 500 escolas; escritas medidas no cliente, com commit, sobre 200
escolas. PostgreSQL 16 em Docker Desktop, Windows 11.

Como reproduzir, com o banco já carregado:

```bash
uv run eduinfra comparar-modelagem
```

| Métrica | A. CRUD (opção nula) | C. Insert-only |
| --- | --- | --- |
| Tamanho após a linha de base | 298 MB | 414 MB |
| Tamanho após 3 ciclos de revisita | 336 MB | 534 MB |
| Registrar uma vistoria, mediana / p95 | 4,11 ms / 7,61 ms | 11,76 ms / 27,01 ms |
| Estado atual de uma escola, mediana / p95 | 0,03 ms / 0,07 ms | 0,12 ms / 0,30 ms |
| Responde a situação em 29/05/2024 depois das revisitas | não | sim |

A alternativa B não aparece na tabela porque ocupa o mesmo espaço e tem o mesmo
custo de C; a diferença entre elas é de correção, não de desempenho.

## Decisão

Escolhemos C. Cada vistoria gera uma nova linha em
[vistoria](../../alembic/versions/0003_vistoria_insert_only.py), e `UPDATE`,
`DELETE` e `TRUNCATE` nas tabelas de evento são bloqueados por gatilho no banco,
não por convenção de aplicação.

A normalização para no grão escola, vistoria e item: uma linha por item vistoriado,
em vez das 17 colunas por escola do arquivo do Censo. Cada item tem situação e
quantidade próprias, e "não informado" precisa valer por item para não sobrescrever
o que a vistoria anterior afirmou. Desnormalizar se paga na camada de serving, onde
a tabela fato é plana para as consultas do assistente, e não na origem.

## Consequências

**O que ganhamos:** o histórico de infraestrutura chega à camada analítica sem CDC
adicional; a E2 pode ingerir por marca d'água sobre `registrado_em`; nenhuma
vistoria some do registro, o que permite auditoria.

**O que perdemos:** 39% a mais de espaço já na linha de base, e cerca de 120 MB a
mais a cada três ciclos de revisita, contra 38 MB do CRUD. A leitura do estado
atual fica 4 vezes mais lenta que no CRUD e passa a depender de `DISTINCT ON` na
view `escola_estado_atual`; ainda está três ordens de grandeza abaixo dos 300 ms
tolerados. Registrar uma vistoria fica perto de 3 vezes mais lento. Corrigir erro
de digitação exige registrar uma retificação, não editar a linha.

**O que se torna irreversível:** a volta é assimétrica. Colapsar o insert-only em
CRUD custa 134 s, o tempo medido para materializar a view em uma tabela. O caminho
contrário não existe: o que um CRUD sobrescreveu não se recupera.

## Gatilho de revisão

- A leitura de `escola_estado_atual` para uma escola passar de 200 ms: a view vira
  materializada, com refresh ao fim de cada carga.
- `vistoria_item` passar de 50 milhões de linhas: entra particionamento por ano do
  evento.
- O aplicativo da secretaria passar a exigir edição direta de vistoria por
  requisito legal: o padrão de retificação precisa ser reavaliado.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Registro inicial da decisão de modelagem da origem | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 22 de setembro de 2026 |
| 1.1 | Evidência atualizada com os números medidos da carga do Censo 2024 | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 22 de setembro de 2026 |
| 1.2 | Estrutura do template da disciplina, opção nula, medição comparativa com CRUD, irreversibilidade e normalização; corrige a data da versão 1.0, que repetia a data da decisão | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 26 de setembro de 2026 |
