# Issue #58 — Recuperação seletiva da fila do CDC

Implementação local em 8 de novembro de 2026, branch `feat/recuperacao-fila-cdc`,
base `e50f6a6`. PostgreSQL 16 dedicado, com `wal_level=logical`, e bancos/Delta
descartáveis. A origem usada na medição é uma cópia da carga nacional do Censo
Escolar 2024, com vistorias sintéticas de testes anteriores.

## Integração das dependências

Os PRs #61, #65, #66 e #67 foram integrados. A revalidação de 10/11/2026 usa
`main` em `7912e44`, incluindo ENEM, fonte canônica, Gold dbt, datas, consulta
otimizada, painel e orquestração. A CLI preserva esses comandos junto dos dois
comandos de recuperação. Métricas e ADR mantêm a validação de datas da #57 e
os procedimentos da #58. Todos os registros de uso de IA foram preservados.

O Draft permanece até a autorização final de Pedro após a conferência das
evidências. As medições e os resultados de 08/11 abaixo são históricos;
a nova rodada integrada está registrada ao final.

## Decisões e procedimento

- `reprocessar-fila-cdc` recupera somente INSERTs rejeitados, consultando cada
  chave primária `(vistoria_id, item_codigo)`. Não chama a publicação completa.
- O destino mantém o merge insert-only: se já houver conteúdo divergente,
  o item permanece pendente. Ausência na origem e data ainda inválida também
  não geram recibo de sucesso.
- O recibo só é escrito depois de confirmar o conteúdo no Delta. Falha entre
  os dois passos permite nova tentativa sem duplicação.
- O registro `tratamentos_cdc` é separado da DLQ e da telemetria. Guarda LSN,
  chave completa, ação, identificador `operador-NN`, instante UTC, motivo fixo,
  chaves revisadas e impressões SHA-256 do estado analítico; não guarda nomes,
  e-mails, observação ou texto livre. O identificador é declarado pelo executor,
  sem autenticação embutida.
- `tratar-desvio-cdc` aceita explicitamente uma ocorrência de UPDATE, DELETE
  ou TRUNCATE e o estado revisado. Não altera a origem nem propaga essas
  operações ao Delta. Não encerra manualmente um INSERT ainda rejeitado.
- A auditoria preserva os contadores históricos e distingue tratamentos de
  pendências. Só dispensa o checksum das chaves cujo estado segue igual ao
  revisado. Uma mudança na mesma chave ou nova ocorrência continua sendo acusada;
  inserir legitimamente outra chave não invalida nem amplia o aceite anterior.
- A identidade do recibo é LSN + chave completa. Lotes legados podem ter
  entradas repetidas para a mesma ocorrência: a seleção deduplica essa identidade
  sem apagar o histórico. Quando há várias alterações na mesma transação, a
  contagem histórica só é integralmente quitada após tratar todas as entradas
  daquele LSN/operação.

O [procedimento operacional](../../metricas-cdc.md#recuperacao-e-tratamento-da-fila-issue-58)
contém os comandos de seleção, reprocessamento e aceite.

## Checklist da issue

- [x] Comando que reprocessa somente itens da fila pela chave na origem.
- [x] Registro de quem tratou, quando e por quê, sem campos pessoais.
- [x] Auditoria dispensa ocorrências tratadas e continua acusando as demais.
- [x] Custo do reprocessamento medido contra a releitura completa.
- [x] Procedimento documentado em `docs/metricas-cdc.md`.

## Critérios de aceite

- [x] Item rejeitado volta ao Delta sem duplicar e sem reler a origem inteira,
  com teste real de integração e falha injetada de esquema.
- [x] Reprocessar a mesma ocorrência duas vezes preserva conteúdo, versões Delta
  e o primeiro recibo; não relê itens já tratados.
- [x] `auditar-cdc` retorna `0` após o tratamento e retorna `1` diante de nova
  violação, inclusive na mesma chave; tratar só uma entrada mantém as demais
  pendentes.

## Medição nacional

A base contém **3.079.057 eventos**, incluindo resíduos sintéticos anteriores.
O script insere uma vistoria sintética de 17 itens na origem descartável e monta
uma fila equivalente a uma rejeição por esquema. Não induz o desvio de coluna
na medição; essa falha é induzida nos testes de integração.

Cada caminho recebe a mesma cópia imutável da base Delta, por hardlinks. A ordem
seletivo/completo alterna a cada rodada. O tempo inclui a leitura e escrita do
caminho; o seletivo inclui os recibos. O completo mede `eventos.carregar_da_origem`,
a leitura/merge usados na publicação completa, e **não** inclui a inicialização
do slot/CLI nem o encerramento da fila. É uma comparação conservadora do custo
de reler toda a origem, sem alegar equivalência operacional entre os comandos.

| Rodada | Seletivo (s) | Completo (s) | Eventos lidos: seletivo / completo |
| --- | ---: | ---: | ---: |
| 1 | 1,198 | 2,786 | 17 / 3.079.074 |
| 2 | 1,299 | 3,158 | 17 / 3.079.074 |
| 3 | 1,218 | 3,243 | 17 / 3.079.074 |
| Mediana | **1,218** | **3,158** | 17 / 3.079.074 |

Nas três rodadas, os destinos terminaram com **3.079.074 linhas e chaves distintas**.
O `bit_xor(hash(...))` de todas as colunas analíticas, exceto `lsn_origem`, foi
`12985952026835883298` em ambos. O LSN distingue os caminhos e não é conhecido
pela releitura completa. Contagem, unicidade e impressão são uma conferência
adicional da medição; não equivalem a uma prova criptográfica linha a linha.
Os testes de integração comparam o conteúdo real e verificam a ausência de duplicação.

Cada repetição posterior retornou `recuperadas=0`, `ja_tratadas=17`,
`pendentes=0`, `linhas_lidas=0`. O preparo inicial da base fica fora dos tempos.
A máquina não estava dedicada, houve outras atividades locais e os caches foram
reutilizados. São três amostras, sem promessa de ganho em outras cargas; o custo
seletivo cresce com a fila e os merges/recibos individuais.

O `EXPLAIN (ANALYZE, BUFFERS)` da consulta por chave retornou uma linha e usou
`Index Scan` em `vistoria_item_pkey`, `vistoria_pkey`, `escola_pkey` e
`municipio_pkey`, com `Nested Loop` entre eles. Na amostra, foram 17 blocos
compartilhados em cache e um lido. Não houve varredura da tabela inteira de itens.

### Reprodução

Somente em uma cópia **descartável e populada** da origem, com dependências do
projeto já instaladas e `DATABASE_URL` exportada:

```bash
PYTHONPATH=src python docs/evidencias/issue-58/medir_reprocessamento.py \
  --confirmar-banco-de-teste --repeticoes 3 --saida /tmp/issue58-medicao.json
```

O script grava uma vistoria sintética, preserva os diretórios temporários usados
na comparação e imprime o JSON de cada rodada. O arquivo de saída inclui as
amostras e o plano PostgreSQL, sem DSN ou valores pessoais. Ele não migra o banco
nem instala dependências.

## Testes

```bash
PYTHONPATH=src python -m pytest \
  tests/test_recuperacao_cdc.py tests/test_recuperacao_cdc_integracao.py \
  tests/test_auditoria_cdc.py tests/test_resiliencia_cdc_integracao.py -q
PYTHONPATH=src python -m pytest --ignore=tests/test_transformacao_property.py -q -rs
python -m mkdocs build --strict --site-dir /tmp/eduinfra-issue58-site
git diff --check
```

A integração cria e remove bancos exclusivos de teste, com falhas provocadas
apenas nesses bancos. Cobre reentrega, falha após escrever no Delta e antes do
recibo, rejeição ainda inválida, Delta divergente, seleção de uma ocorrência,
aceite de UPDATE/DELETE/TRUNCATE/cabeçalho, duas alterações da mesma chave no
mesmo LSN, nova violação na mesma chave, novas rejeições apesar de um estado
anterior aceito, mudanças antes de nova passada do consumidor, preservação de
outras pendências e identificação operacional sem texto livre.

Na rodada inicial, a suíte opcional de propriedades não foi executada: `hypothesis` não está
instalado e nenhuma dependência foi instalada nesta tarefa. Os testes de frescor
que exigem Gold ficaram pendentes naquela rodada; foram executados na revalidação abaixo.

### Resultado da verificação inicial (08/11/2026)

```text
236 passed, 5 skipped, 11 warnings in 68.93s
```

Os cinco skips são exclusivamente de `tests/test_frescor_integracao.py`, por
Gold não construída nesse banco. As onze advertências são de APIs de particionamento
já depreciadas em `comparacao_latencia.py`, fora do escopo desta entrega.
`mkdocs build --strict` terminou sem erro; `git diff --check` também passou.

A conferência direcionada anterior passou em 68 testes (recuperação, auditoria e
resiliência); a suíte final acima inclui ainda o caso de nova rejeição em uma
chave cujo desvio anterior já havia sido aceito.

## Limites operacionais

O histórico da DLQ e os recibos crescem e permanecem disponíveis. A recuperação
não apaga ou corrige dados insert-only, não reconstrói conteúdo excluído e não
converte datas inválidas silenciosamente. O aceite é uma decisão explícita de
conviver com um estado divergente, sem mascarar mudanças futuras.

A leitura dos estados de um cabeçalho/TRUNCATE pode ser ampla, e o recibo guarda
as chaves revisadas. O tratamento deve ser serializado; uma disputa de criação/
escrita Delta exige nova tentativa. Problemas de infraestrutura continuam falhando
em vez de gerar recibo. Slot, frescor e desvios atuais de catálogo continuam sendo
avaliados normalmente, mesmo após o tratamento da fila.

## Aceite integrado — 10/11/2026

Base integrada: `main` em `7912e44`, após os merges de #61, #65, #66 e #67.
Os quatro conflitos foram resolvidos na CLI, uso de IA, métricas e ADR 0007.
O código de recuperação e os testes não precisaram de alterações funcionais.

### Ambiente e preservação do histórico

A validação usou PostgreSQL 16 em instância/volume próprios. Uma cópia da origem
de teste nacional, com resíduos sintéticos anteriores, foi restaurada nesse
ambiente. O banco pessoal e o banco do painel não foram alterados. A Silver
oficial do Censo/ENEM já disponível localmente foi copiada; não houve novo
download do INEP. O runner recebeu `FONTE_ENEM` para consumir esse retrato.

| Upgrade da origem copiada | Antes (0007) | Depois (0009) |
| --- | --- | --- |
| Vistorias | 181.148 | 181.148 |
| Impressão integral do histórico | `e1650cf4f8494e89b368612e401a913e` | `e1650cf4f8494e89b368612e401a913e` |

Foram preservados o manifesto 0008 do ENEM e a regra de datas 0009. A cópia
continha 181.065 escolas e a fonte canônica inicial teve 3.079.516 eventos.
A Gold completa foi construída pelo runner dbt: **13 modelos e 26 testes,
PASS=39**, sem WARN/ERROR/SKIP. O fato ENEM teve 25.179 escolas e o painel foi
gerado pela CLI integrada. O frescor estava em dia antes dos cenários que
inserem vistorias de propósito.

### Testes da integração

- Rodada direcionada de recuperação/auditoria/resiliência: **69 passed in 31.08s**.
- Suíte completa, com todos os arquivos `test_*.py`: **360 passed, 12 warnings
  in 168.62s**, sem skips ou exclusão de arquivos.
- Inclui os cinco testes de frescor antes pendentes e a suíte de propriedades
  com Hypothesis. A dependência opcional ficou no ambiente isolado de testes,
  sem alteração dos arquivos de dependências.

A auditoria da Gold foi executada primeiro, com origem e Gold no mesmo retrato;
os cenários seguintes inserem vistorias deliberadamente e tornam esse retrato
antigo. Essa ordenação permite conferir a auditoria e todos os cenários na
mesma execução, sem ignorar testes. Reprodução após construir a Gold, com
`DATABASE_URL` e `DIRETORIO_DADOS` apontando ao ambiente descartável:

```bash
uv run --group transformacao --with hypothesis python - <<'PYTEST'
from pathlib import Path
import pytest
auditoria = Path('tests/test_e3_gold.py')
arquivos = [str(auditoria)] + [
    str(p) for p in sorted(Path('tests').glob('test_*.py')) if p != auditoria
]
raise SystemExit(pytest.main([*arquivos, '-q', '-rs']))
PYTEST
```

Uma primeira rodada direcionada, simultânea à preparação pesada dos dados,
terminou com 68 aprovados e um erro: a recuperação ocorreu, mas a auditoria
acusou frescor de 75 s, acima do SLA de 60 s. O cenário passou isolado e a
rodada direcionada completa acima passou sem carga concorrente. O limite de
produção e os testes foram preservados. As advertências finais são onze de
APIs de particionamento existentes e uma de coleta de asset do Dagster;
não foram corrigidas fora do escopo.

### Medição repetida após a integração

A suíte deixou vistorias sintéticas na origem descartável. A nova linha de base
teve 3.079.992 eventos; a medição adicionou uma vistoria de 17 itens. Foram
três rodadas, alternando a ordem, com as mesmas cópias Delta de partida:

| Rodada | Seletivo, com recibos (s) | Leitura/merge completos (s) |
| --- | ---: | ---: |
| 1 | 1,339 | 2,847 |
| 2 | 1,319 | 2,884 |
| 3 | 1,315 | 2,823 |
| Mediana | **1,319** | **2,847** |

O seletivo leu **17 itens**; o completo leu **3.080.009 eventos**. Os dois
destinos tiveram 3.080.009 linhas e chaves distintas, com a mesma impressão
das colunas analíticas, exceto LSN: `11910358003474991727`. Essa impressão é
uma conferência adicional de 64 bits, não uma prova criptográfica de igualdade.
O teste de integração confere os eventos efetivamente recuperados e sua
repetição. Nas três repetições da medição houve 17 já tratados, zero linhas
relidas, zero recuperações novas e zero pendências.

O plano da consulta por chave continuou usando `Index Scan` nos índices
`vistoria_item_pkey`, `vistoria_pkey`, `escola_pkey` e `municipio_pkey`, retornando
uma linha e sem varrer a tabela inteira de itens. A máquina não estava dedicada,
os caches estavam aquecidos e os números não garantem esse ganho em outras
cargas. O escopo de custo continua sendo leitura/merge completos versus
recuperação seletiva com recibos; não inclui inicialização de slot/CLI no completo.

Após a suíte e a medição, a fonte canônica foi republicada com 3.080.009 eventos
e a Gold foi reconstruída: **PASS=39, WARN=0, ERROR=0, SKIP=0**. Isso atualiza
o retrato analítico após as inserções sintéticas da validação.
