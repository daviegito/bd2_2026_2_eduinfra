# 0004 — Combinar lote idempotente do Censo com CDC pelo slot lógico do PostgreSQL

- **Status:** proposto — registra a implementação integrada; o texto aguarda revisão da Squad
- **Data:** 2026-10-18
- **Decisores:** Squad EduInfra

## Contexto

A origem insert-only contém 181.065 escolas, 181.065 vistorias e 3.078.105 itens do Censo Escolar 2024: 17 itens por vistoria, com carga anual. Estimam-se 500 vistorias/dia (8.500 itens/dia) na secretaria; essa taxa ainda não foi medida em produção.

A E2 exige lote e CDC complementares, sem duplicatas após reexecução. É necessário preservar o histórico e a data de ocorrência para comparar infraestrutura e desempenho no ENEM. Restrições: execução local pelo Compose, sem serviços pagos; reutilizar Python/PostgreSQL; manter a identificação do técnico na origem.

## Alternativas consideradas

### A. Opção nula — consultar periodicamente o OLTP por marca d’água

Consultar itens novos por `registrado_em` em SQL agendado. Dispensa slot e consumidor contínuo e venceu nos lotes maiores medidos. É viável no volume atual, mas transações confirmadas depois da marca podem escapar; a margem de dez minutos não cobre transações mais longas. Exige reconciliação e, isoladamente, não atende ao CDC da E2.

### B. Lote idempotente e consumidor próprio de `pgoutput` — escolhida

Combinar a carga anual com o WAL de `vistoria` e `vistoria_item`. Reutiliza Python/PostgreSQL e captura commits sem depender de `registrado_em`. Exige manter decodificador, consumidor e recuperação do slot; cobre somente inserções nessas tabelas.

### C. Lote idempotente e Debezium com Kafka Connect/Kafka ou Redpanda

Alternativa para ampliar operações e consumidores, acrescentando serviços e monitoramento. **Não foi prototipada nem medida**; não se declara vantagem quantitativa sobre ela.

## Medição

O script `src/eduinfra/comparacao_ingestao.py` mede **somente captura**, sem gravação no Delta, sobre a origem do domínio com inserções de teste. Resultados registrados pela equipe em Docker Desktop/Windows: uma execução por volume; vinte leituras ociosas. A consulta incremental não tinha índice em `registrado_em`.

| Vistorias novas | Itens | A — incremental (ms) | B — slot lógico (ms) |
| ---: | ---: | ---: | ---: |
| 1 | 17 | 72,1 | 19,7 |
| 100 | 1.700 | 27,6 | 54,6 |
| 5.000 | 85.000 | 1.011,0 | 4.890,7 |
| Nenhuma, mediana de 20 | 0 | 34,1 | 116,3 |

As contagens coincidiram. Escolhemos CDC pelo requisito da E2 e pela captura de commits, não por velocidade. Latência de ponta a ponta e p95 não foram medidos; os 300 ms da leitura transacional não são SLA de ingestão.

Como reproduzir, em ambiente de desenvolvimento com a origem carregada:

```bash
docker compose run --rm carga comparar-ingestao
docker compose run --rm carga demonstrar-cdc
docker compose run --rm carga demonstrar-atraso
```

Os comandos inserem dados de teste: usar ambiente de desenvolvimento. Na demonstração registrada, o CDC reentregou 34 itens após falha e inseriu zero duplicatas. A janela de 3.078.105 itens produziu a mesma impressão em 7,9 s e 8,1 s, com máximo de 755 MB. São medições existentes, não novas execuções deste ADR.

## Decisão

Adotar B, com as seguintes garantias e limites:

- **Lote:** a última execução do ano em `ingestao_execucao` identifica a versão vigente por SHA-256. Só ignorar o arquivo quando o manifesto e o marcador `origem.sha256` da Silver coincidirem com ele. Preparar a Silver separadamente, confirmar carga e manifesto na mesma transação e então publicar o diretório do ano. A reexecução recupera uma publicação pendente sem recarregar o banco. Arquivo corrigido substitui a Silver, mas o OLTP recebe somente chaves novas; retificar eventos exige nova vistoria, conforme o ADR 0001.
- **CDC:** usar a publicação/slot `eduinfra_cdc`, ler com `pg_logical_slot_peek_binary_changes`, gravar no Delta e só então avançar o LSN. O consumidor passa a cada dois segundos. A entrega é **no mínimo uma vez, com efeito idempotente** pelo `merge` de `(vistoria_id, item_codigo)`; isso não constitui transação distribuída entre PostgreSQL e Delta.
- **Atraso:** particionar eventos pelo mês de `ocorrido_em`. O caminho `processar-janelas` usa `registrado_em` com margem de dez minutos, tolerância de sete dias após o fim do mês e reabre janelas para atrasos maiores, sem descartar eventos. O reprocessamento mensal é idempotente. Esse caminho em Parquet e o CDC em Delta **coexistem**; a troca automática do controle `_controle.json` por LSN e a unificação dos destinos não estão implementadas.

## Consequências

**O que ganhamos:** carga reexecutável com manifesto, recuperação após falhas, histórico preservado e captura por ordem de commit. Parada e reentrega do consumidor não duplicam itens no destino.

**O que perdemos:** o consumidor próprio foi mais lento no lote de 85.000 itens e exige manter o protocolo e a operação do slot. O limite configurado de WAL retido é 1 GB; sua invalidação exige recuperar o fluxo e reconciliar o destino. Não há commit conjunto entre banco e arquivos: falha após o commit pode deixar a Silver antiga até nova execução. Entre os dois renames, a partição pode ficar temporariamente ausente. Reprocessar uma única correção pode custar a janela inteira, cerca de oito segundos no volume medido, e alterar números de meses já fechados. A duplicidade de caminhos analíticos aumenta o trabalho de operação e reconciliação.

**O que se torna irreversível:** descartar o slot elimina seu ponto de retomada. A arquitetura pode mudar, mas exige nova carga de base, checkpoints e reconciliação; seu custo de migração não foi medido.

## Gatilho de revisão

- Revisar o consumidor próprio ao surgir `UPDATE`/`DELETE`, um segundo consumidor do fluxo ou atraso do slot acima de **100 MB** em operação normal.
- Rever a estratégia de janelas se um reprocessamento superar **um minuto** ou **1 GB de memória**.
- Rever a tolerância se eventos fora dos sete dias ultrapassarem **5% em um mês**.

## Evidências

- [Caracterização da carga](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/268ed4986958ef23fa157fc0fb02ddcf99ebad79/docs/caracterizacao.md).
- [CDC, semântica, benchmark e demonstração](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/268ed4986958ef23fa157fc0fb02ddcf99ebad79/docs/cdc-e-semantica-de-entrega.md).
- [Janelas, dados atrasados e medições](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/268ed4986958ef23fa157fc0fb02ddcf99ebad79/docs/dado-atrasado.md).
- [Lote e recuperação após commit](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/268ed4986958ef23fa157fc0fb02ddcf99ebad79/src/eduinfra/ingestao.py), [testes de ingestão](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/268ed4986958ef23fa157fc0fb02ddcf99ebad79/tests/test_ingestao.py) e [PR #36 integrada](https://github.com/daviegito/bd2_2026_2_eduinfra/pull/36).

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Consolida as decisões e evidências de ingestão da E2 | [Pedro Vargas](https://github.com/Pedrovargas10) | Pendente | 18 de outubro de 2026 |
