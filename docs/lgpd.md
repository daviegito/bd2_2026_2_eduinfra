# LGPD: análise do que foi construído

Esta página analisa, dado por dado, o que a plataforma guarda. Para cada dado
pessoal, diz sob que base legal ele é tratado, por quanto tempo fica e como é
descartado. Responde à issue #87.

O resumo é curto. Só um lugar do sistema guarda dado pessoal direto: a tabela
`tecnico` da origem, com nome, matrícula e UF de lotação de quem registra.
Hoje ela só tem contas sintéticas. Censo e ENEM entram agregados por escola.
O único arquivo que tem uma linha por pessoa é o microdado bruto do ENEM, que
o INEP publica pseudonimizado e que fica no disco de quem roda o projeto, fora
do repositório.

## Inventário por tabela e camada

"Pessoal" segue o art. 5º, I, da LGPD: informação relacionada a pessoa natural
identificada ou identificável. "Pseudonimizado" é dado pessoal cuja ligação com a
pessoa depende de uma informação guardada à parte (art. 13, § 4º). Dado de escola
e de município não é dado pessoal.

| Camada | Onde | O que guarda | Classificação |
| --- | --- | --- | --- |
| Origem | `tecnico` | `nome`, `matricula`, `lotacao_uf` de quem registra | **Pessoal**. Hoje só contas sintéticas: `SISTEMA-CENSO` (carga), `DEMO-E2-27`, `DEMO-E3-52` (demonstrações) e `SAMPLE-GEN` (gerador de amostra) |
| Origem | `vistoria` | `tecnico_id`, escola, origem, datas, `observacao` | **Pseudonimizado**: o `tecnico_id` liga o registro à pessoa por meio da tabela `tecnico`. A `observacao` é texto livre; a carga grava só um texto fixo ("Linha de base derivada do Censo Escolar 2024") |
| Origem | `vistoria_item` | item e situação por registro | Não pessoal |
| Origem | `escola`, `municipio`, `item_infraestrutura`, `escola_estado_atual` | cadastro público do Censo e catálogo de itens | Público, não pessoal |
| Origem | `ingestao_execucao` | arquivo, contagens e horários de cada carga | Não pessoal |
| Origem | `monitoramento.frescor_gold` | medições de frescor | Não pessoal |
| Bruto | `dados/bruto/` (Censo) | ZIP dos microdados do Censo; o projeto lê só a tabela de escolas | Público, não pessoal |
| Bruto | `dados/bruto/enem/` | ZIP do ENEM 2024: `RESULTADOS` tem uma linha por inscrito (`NU_SEQUENCIAL`, escola e notas); `PARTICIPANTES` traz o questionário | **Pseudonimizado e público**: sem nome nem CPF, publicado pelo INEP |
| Silver | `silver/ano_censo=…` | escolas, municípios e itens do Censo em Parquet | Público, não pessoal |
| Silver | `silver/enem/ano=…` | médias e contagens **por escola** | Agregado, não pessoal |
| Delta | `warehouse/vistoria_item` | ano, UF, município, escola, rede, localização, item e situação | Não pessoal |
| Delta | `eventos_vistoria` | registro, item, escola, UF, origem, datas, situação e LSN. **Sem `tecnico_id`** | Não pessoal |
| Delta | fila de erros do CDC | LSN, chave, tabela, operação, `vistoria_id`, item e motivo | Não pessoal |
| Delta | telemetria do CDC | tempos e contagens por transação | Não pessoal |
| Delta | `tratamentos_cdc` | `responsavel` no formato `operador-NN` | Pseudonimizado: a CLI recusa nome ou e-mail (`recuperacao_cdc.validar_operador`) |
| WAL e slot | `pg_wal`, slot `eduinfra_cdc` | todas as mudanças da origem, inclusive em `tecnico`; o CDC decodifica só `vistoria` e `vistoria_item` | Contém dado pessoal enquanto o segmento não é reciclado |
| Gold | `dim_*`, `fato_*` | escolas, municípios, itens, perfis e desempenho por escola | Público ou agregado, não pessoal |
| Métricas | `metricas/*.yml` | definições, sem dado | Não se aplica |
| Painel | `painel.html` (site público) | médias de grupos de escolas, com supressão de grupos com menos de 10 escolas | Agregado, não pessoal |
| Logs | saída da CLI e do Dagster | contagens e mensagens; o `anonimizar-tecnico` não repete nome nem matrícula | Não pessoal |

O dado pessoal da tabela `tecnico` não desce para as camadas analíticas. A
publicação `eduinfra_cdc` só inclui `vistoria` e `vistoria_item`
(`alembic/versions/0005_habilita_cdc_logico.py`). Do técnico, a Delta, a Gold e o
painel recebem no máximo o `tecnico_id`, e hoje nem ele chega à Delta.

## Base legal, retenção e descarte

Só as linhas com dado pessoal ou pseudonimizado entram aqui. As bases legais são
a leitura da squad e estão **a confirmar com a orientação da disciplina**.

| Dado | Finalidade | Base legal proposta | Retenção | Descarte |
| --- | --- | --- | --- | --- |
| `tecnico` (nome, matrícula, UF) | saber quem registrou cada informação de infraestrutura e recortar o acesso por UF (#86) | art. 7º, III: execução de política pública pela administração | enquanto a pessoa estiver em atividade | `eduinfra anonimizar-tecnico --matricula …`, ao fim do vínculo ou a pedido do titular (art. 18, IV) |
| `vistoria.tecnico_id` | ligar o registro a quem o fez | a mesma do técnico | permanente: o histórico é insert-only (ADR 0001) | não se apaga; deixa de identificar a pessoa quando o técnico é anonimizado (art. 12) |
| `vistoria.observacao` | contexto do registro | a mesma do técnico | permanente | não se apaga; a regra é não escrever dado pessoal no texto livre |
| ENEM bruto (`RESULTADOS` e `PARTICIPANTES`) | calcular a média por escola | art. 7º, § 3º (dado de acesso público, usado na finalidade que justificou a publicação) e art. 7º, IV (estudo por órgão de pesquisa) | só o tempo de gerar a Silver e de reprocessar | apagar `dados/bruto/enem/` depois de publicar a Silver; o manifesto guarda o hash e permite baixar de novo |
| `tratamentos_cdc.responsavel` | auditoria de quem tratou um item da fila | art. 7º, IX (legítimo interesse: segurança e auditoria) | enquanto existir o histórico da fila | o identificador `operador-NN` não tem nome; a tabela que liga o código à pessoa fica fora do sistema |
| WAL | replicação e recuperação | a mesma do dado de origem | até a reciclagem do segmento; o slot segura no máximo `max_slot_wal_keep_size` (2 GB no Compose) | reciclagem automática depois do checkpoint, com o slot confirmado |

## Eliminação contra imutabilidade

A origem é insert-only (ADR 0001). Triggers bloqueiam UPDATE, DELETE e TRUNCATE em
`vistoria` e `vistoria_item`, e cada registro aponta para `tecnico.id`. Se o
técnico pedir a eliminação dos seus dados, a linha não pode sumir: isso quebraria
a chave estrangeira de todo o histórico.

A saída escolhida é **anonimizar o técnico e manter o id**. A tabela `tecnico`
não é insert-only, então o comando `anonimizar-tecnico`
(`src/eduinfra/origem/anonimizacao.py`) faz quatro coisas:

- troca o nome por "Técnico anonimizado";
- troca a matrícula por `anonimizado-<id>`, que continua única;
- desativa o cadastro (`ativo = false`);
- mantém o `id` e a `lotacao_uf`, e não toca em nenhuma vistoria.

Depois disso, os registros continuam válidos e contáveis, mas não apontam mais
para uma pessoa. Pelo art. 12, dado anonimizado deixa de ser dado pessoal.

O comando falha (código 1) se a matrícula não existir, inclusive quando o técnico
já foi anonimizado, e não altera nada nesse caso. Matrícula vazia ou com o
prefixo `anonimizado-` dá código 2.

### Demonstração

A saída real, num banco descartável com um técnico e duas vistorias fictícios,
está em [evidencias/lgpd/anonimizacao.txt](evidencias/lgpd/anonimizacao.txt):

| | `matricula` | `nome` | `ativo` | vistorias com `tecnico_id = 1` |
| --- | --- | --- | --- | --- |
| Antes | `T-0042` | Pessoa Fictícia da Silva | `t` | 2 |
| Depois | `anonimizado-1` | Técnico anonimizado | `f` | 2 |

Na mesma evidência, repetir o comando sai com código 1 sem mudar nada, e um
UPDATE em `vistoria` continua bloqueado pela trigger de insert-only.

Os testes em `tests/origem/test_anonimizacao_integracao.py` cobrem três casos, cada um
num banco criado e apagado pelo teste:

- o técnico é anonimizado, o outro técnico fica intacto e as vistorias ficam
  idênticas antes e depois;
- repetir o comando não muda nada;
- matrícula inexistente, ausente ou já anonimizada é recusada.

Eles rodam no comando de testes de integração (`pytest -m integracao`).

### O que a anonimização não alcança

- **Versão antiga da linha.** No PostgreSQL, o UPDATE não sobrescreve a linha:
  grava uma nova versão. A antiga fica no arquivo da tabela até o VACUUM limpar.
  Ela também fica nos segmentos do WAL até serem reciclados. Depois de anonimizar,
  convém rodar `VACUUM FULL tecnico`, que reescreve a tabela e é barato porque ela
  é pequena. O comando ainda não faz isso.
- **Cópias de segurança.** O projeto não tem rotina de backup. Se tiver, o
  técnico anonimizado continua nas cópias anteriores até elas vencerem.
- **Padrão de trabalho.** A UF de lotação e as datas e escolas dos registros
  continuam. Numa UF com um único técnico, quem souber quem trabalhou ali naquela
  época ainda consegue ligar os registros à pessoa. Para a squad, esse risco é
  aceitável, porque a UF sustenta o recorte de acesso. A alternativa seria
  generalizar a UF na anonimização, e essa decisão fica com o grupo.

## Risco de reidentificação ao cruzar ENEM e Censo

O alerta da E4 é que duas bases anonimizadas, cruzadas, podem reidentificar quem
nenhuma das duas identificava sozinha. No projeto, o cruzamento é ENEM × Censo
por escola (`CO_ESCOLA` = `CO_ENTIDADE`, [ENEM e junção com o Censo](enem.md)).

**O que já reduz o risco**

- **Máscara do INEP abaixo de 10 participantes.** Quando a escola tem menos de 10
  participantes, o INEP troca o `CO_ESCOLA` por uma máscara. Esses códigos não
  existem no Censo e não entram na junção. Toda média por escola na Silver e na
  Gold tem, portanto, pelo menos 10 inscritos.
- **Só os resultados entram.** Desde 2024, `RESULTADOS` e `PARTICIPANTES` não
  têm chave em comum (`NU_SEQUENCIAL` não é `NU_INSCRICAO`), e o pipeline só lê
  `RESULTADOS`. Assim, o perfil socioeconômico do aluno (sexo, idade, cor/raça,
  renda) nunca é ligado à escola nem às notas.
- **Nada no grão da pessoa a partir da Silver.** A Silver do ENEM já sai agregada
  por escola (`src/eduinfra/ingestao/enem.py`). Nenhuma camada a partir dela tem uma
  linha por inscrito.
- **Supressão no painel.** O painel suprime a média de qualquer grupo com menos
  de 10 escolas com nota válida, inclusive depois de filtrar (`MINIMO_ESCOLAS`
  em `src/eduinfra/consumo/painel.py`).

**O que sobra**

- **Escola no limite de 10 participantes.** Quem conhece as notas de 9 dos 10
  alunos consegue deduzir a do décimo pela média da escola. A Gold guarda a média
  por escola, e a lista de escolas do topo e da base (#94, #95) vai expô-la. É o
  mesmo nível de detalhe que o INEP já publica, mas a squad deve decidir se a
  lista mostra a média exata ou uma faixa.
- **Diferença entre filtros.** Comparar dois recortes que diferem por uma única
  escola pode isolar a média dela. A média de uma escola é agregado de pelo menos
  10 pessoas, então isso não chega a uma pessoa, mas é um motivo para manter a
  supressão como parte da definição da métrica (#82) e não só do painel.
- **Microdado bruto no disco.** O ZIP do ENEM fica em `dados/bruto/enem/`, com
  uma linha por inscrito. Ele não vai para o Git (`.gitignore` ignora `dados/` e
  `*.zip`), mas fica na máquina de quem roda o projeto até ser apagado à mão.

## Pendências

- Confirmar as bases legais com a orientação da disciplina.
- Revisão do documento pelo grupo.
- Rodar `VACUUM FULL tecnico` ao final do `anonimizar-tecnico`, ou registrar o
  passo no procedimento.
- Automatizar o descarte de `dados/bruto/enem/` depois da publicação da Silver.
- Incluir no inventário a tabela do ETL reverso (#85) e os papéis do controle de
  acesso (#86) quando forem mergeados.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Inventário por dado, bases legais, anonimização do técnico com demonstração e risco de reidentificação | [Gabriel Lopes de Amorim](https://github.com/BrzGab) |  | 24 de novembro de 2026 |
