# Retenção de WAL durante a Gold — issue #60

Correção desenvolvida na branch `fix/cdc-slot-ocioso`, a partir de `6c65929`. Mantidos o PostgreSQL compartilhado, a publicação, o slot e o formato dos eventos.

## Resultado

| Caminho | Escolas | Itens na origem antes | Limite | WAL gerado (bytes) | Pico retido (bytes) | Retido final (bytes) | Estado final |
| --- | ---: | ---: | --- | ---: | ---: | ---: | --- |
| SQL direto | 181065 | 3078105 | 1GB | 985449528 | 985449528 | 408 | reserved |
| dbt + preparo das fontes + testes + docs | 181065 | 3078581 | 2GB | 1868134216 | 1196987160 | 17752 | reserved |

A passagem SQL levou 37,901 s; o fluxo dbt completo, 109,030 s. Esses tempos incluem a inserção do marcador, e o segundo inclui o preparo das fontes, testes e documentação. Não são um benchmark de desempenho entre ferramentas. O WAL inclui o job, o marcador e checkpoints.

Em cada cenário, uma vistoria foi inserida com a construção em andamento. Seus 17 itens chegaram ao Delta e tinham 17 chaves distintas. Nenhum erro do consumidor foi registrado. Durante o dbt observaram-se estados `reserved` e `extended`, sem `unreserved` ou `lost`.

## Condições e limites

- PostgreSQL 16 em contêineres dedicados, execução local Linux; máquina compartilhada com outras aplicações, sem alegação de hardware dedicado.
- Censo Escolar 2024 nacional: 181.065 escolas e 3.078.105 itens na rodada SQL. A rodada dbt reutiliza um dump público de desenvolvimento, com 476 itens de vistorias sintéticas adicionais de testes anteriores; isso explica os 3.078.581 itens.
- Consumidor corrigido a cada 2 s. Monitoramento a cada 0,2 s e CHECKPOINT forçado a cada 10 s, além de outro ao terminar. Os testes da suíte não concorreram no cluster de medição válido.
- SQL medido com limite de 1 GB. Passou, mas reteve aproximadamente 940 MiB numa transação: margem pequena para aumento dos dados e atraso prévio.
- O job dbt com preparo das fontes reteve aproximadamente 1.142 MiB, acima de 1 GB. Por isso o padrão operacional é 2 GB, configurável e finito. Isso não elimina a necessidade de drenar nem garante capacidade para volumes futuros.
- O caso dbt foi validado num snapshot que combina a #48 local e somente os dois módulos corrigidos da #60. As branches e as PRs dos colegas não foram mescladas ou modificadas.
- Uma tentativa anterior de mudar o limite por reload manteve 1 GB, pois a opção da linha de comando tinha precedência; o preflight recusou o job. A rodada válida usou outra instância iniciada explicitamente com 2 GB.

## Reproduzir

Usar banco de desenvolvimento populado e arquivos de dados correspondentes à E2, com `DATABASE_URL` e `DIRETORIO_DADOS` exportadas. O script cria a linha de base e o slot se necessário. **Ele insere uma vistoria marcada e recria a Gold; nunca apontar para produção.**

```bash
uv run python docs/evidencias/issue-60/medir_wal.py --confirmar-banco-de-teste --saida /tmp/issue60-sql.json
```

Para medir o dbt, integrar também a #48 e suas dependências, usar retenção de pelo menos 2 GB e passar o comando ao mesmo script:

```bash
uv run --group transformacao python docs/evidencias/issue-60/medir_wal.py --confirmar-banco-de-teste --saida /tmp/issue60-dbt.json --comando uv run --group transformacao python transformacao/scripts/executar.py
```

O script mantém o consumidor em uma thread durante toda a construção, força checkpoints e escreve todas as amostras no JSON indicado. O limite pode ser configurado no Compose por `MAX_SLOT_WAL_KEEP_SIZE`; requer recriar o serviço banco para trocar sua opção de inicialização, sem remover o volume.

## Checklist e aceite

| Item | Evidência |
| --- | --- |
| Capturar WAL persistido antes do peek | `cdc.posicao_persistida`, usando `pg_current_wal_flush_lsn`; posição passada como limite explícito da leitura |
| Escrita fora da publicação não prende o slot | testes de avanço do confirmed_flush_lsn e do restart_lsn depois de checkpoint |
| Transação aberta durante passada vazia não se perde | teste com duas conexões: commit posterior chega ao Delta uma vez |
| Commit entre captura e peek não é pulado | teste que insere depois da captura e entrega na passada seguinte |
| Limite de leitura e reentrega preservados | testes de leitura parcial e falha antes de confirmar, sem duplicação |
| Medir retenção durante e após a Gold | script e duas rodadas nacionais acima, com consumidor ativo e checkpoint |
| Dimensionar e documentar | padrão de 2 GB finitos no Compose, parâmetros e limites documentados |
| Gold termina sem slot perdido | SQL e dbt terminaram com slot reserved e marcador entregue; teste Compose de volumes vazios registrado abaixo |

## Verificação das PRs abertas

Comparação de três vias com as branches das PRs #61, #62, #63, #64 e #65: **nenhum conflito textual com o patch da #60**. Isso não é garantia de ausência de todo conflito futuro. O código do CDC não é alterado por essas PRs e o parâmetro novo de leitura é opcional.

- #61: os oito testes de regras de plano passaram em um snapshot da própria PR com os módulos corrigidos.
- #62: os dez testes de frescor/CLI passaram em outro snapshot da própria PR com os módulos corrigidos.
- #63: mantém o contrato das tabelas da Gold e o comando construir-gold. Não é necessário editar sua CLI, seus testes ou Dockerfile para corrigir o CDC.
- #64: não altera o consumidor; sua escolha de dbt e suas evidências permanecem preservadas.
- #65: o único arquivo compartilhado é o Compose; a alteração de retenção se aplica sem conflito com os mounts da PR. O painel ainda é um rascunho e seu contrato de ENEM precisa ser alinhado com a #44/#48, independentemente da #60.

## Testes

`pytest tests --ignore=tests/test_transformacao_property.py -q`: **201 passaram**, com 11 avisos de depreciação já existentes, em 56,69 s. O arquivo opcional de Hypothesis ficou explicitamente de fora, pois a dependência não estava instalada. Os seis testes específicos de slot estão incluídos nessa contagem.

## Saídas e amostras das medições

### SQL

<details markdown="1">
<summary>Resumo e 209 amostras coletadas</summary>

```json
{
  "data_utc": "2026-11-08T15:50:11.717255+00:00",
  "comando": [
    "construir-gold (SQL)"
  ],
  "escolas": 181065,
  "itens_antes": 3078105,
  "limite_slot": "1GB",
  "intervalo_consumidor_s": 2,
  "checkpoint_s": 10,
  "duracao_construcao_e_insercao_s": 37.901,
  "wal_gerado_bytes": 985449528,
  "pico_retido_bytes": 985449528,
  "retido_final_bytes": 408,
  "wal_status_final": "reserved",
  "estados_observados": [
    "reserved"
  ],
  "passadas": 14,
  "itens_do_marcador": 17,
  "chaves_do_marcador": 17,
  "itens_esperados_do_marcador": 17,
  "erros": [],
  "amostras": [
    {
      "segundos": 0.01,
      "wal_retido_bytes": 56,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 0.212,
      "wal_retido_bytes": 19575592,
      "atraso_bytes": 19575536,
      "wal_status": "reserved"
    },
    {
      "segundos": 0.413,
      "wal_retido_bytes": 23597864,
      "atraso_bytes": 23597808,
      "wal_status": "reserved"
    },
    {
      "segundos": 0.614,
      "wal_retido_bytes": 26768168,
      "atraso_bytes": 26768112,
      "wal_status": "reserved"
    },
    {
      "segundos": 0.816,
      "wal_retido_bytes": 27783976,
      "atraso_bytes": 27783920,
      "wal_status": "reserved"
    },
    {
      "segundos": 1.016,
      "wal_retido_bytes": 27787352,
      "atraso_bytes": 27787296,
      "wal_status": "reserved"
    },
    {
      "segundos": 1.217,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 27819192,
      "wal_status": "reserved"
    },
    {
      "segundos": 1.418,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 27819192,
      "wal_status": "reserved"
    },
    {
      "segundos": 1.619,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 27819192,
      "wal_status": "reserved"
    },
    {
      "segundos": 1.82,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 27819192,
      "wal_status": "reserved"
    },
    {
      "segundos": 2.021,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 27819192,
      "wal_status": "reserved"
    },
    {
      "segundos": 2.221,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 2.422,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 2.623,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 2.824,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 3.025,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 3.225,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 3.427,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 3.628,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 3.828,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 4.029,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 4.23,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 4.431,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 4.631,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 4.832,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 5.033,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 5.234,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 5.434,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 5.635,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 5.836,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 6.037,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 6.237,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 6.438,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 6.639,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 6.84,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 7.041,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 7.242,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 7.442,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 7.643,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 7.844,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 8.045,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 8.245,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 8.446,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 8.647,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 8.848,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 9.049,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 9.249,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 9.45,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 9.651,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 9.852,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 10.053,
      "wal_retido_bytes": 27819248,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 10.303,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 800,
      "wal_status": "reserved"
    },
    {
      "segundos": 10.504,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 10.705,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 10.906,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 11.106,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 11.307,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 11.508,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 11.709,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 11.91,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 12.11,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 12.311,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 12.513,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 12.714,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 12.914,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 13.115,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 13.316,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 13.517,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 13.717,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 13.918,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 14.119,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 14.32,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 14.521,
      "wal_retido_bytes": 27820048,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 14.721,
      "wal_retido_bytes": 27820328,
      "atraso_bytes": 280,
      "wal_status": "reserved"
    },
    {
      "segundos": 14.922,
      "wal_retido_bytes": 27820328,
      "atraso_bytes": 280,
      "wal_status": "reserved"
    },
    {
      "segundos": 15.123,
      "wal_retido_bytes": 27820328,
      "atraso_bytes": 280,
      "wal_status": "reserved"
    },
    {
      "segundos": 15.323,
      "wal_retido_bytes": 27820328,
      "atraso_bytes": 280,
      "wal_status": "reserved"
    },
    {
      "segundos": 15.524,
      "wal_retido_bytes": 27820328,
      "atraso_bytes": 280,
      "wal_status": "reserved"
    },
    {
      "segundos": 15.725,
      "wal_retido_bytes": 27820328,
      "atraso_bytes": 280,
      "wal_status": "reserved"
    },
    {
      "segundos": 15.926,
      "wal_retido_bytes": 27820328,
      "atraso_bytes": 280,
      "wal_status": "reserved"
    },
    {
      "segundos": 16.127,
      "wal_retido_bytes": 27882280,
      "atraso_bytes": 62232,
      "wal_status": "reserved"
    },
    {
      "segundos": 16.327,
      "wal_retido_bytes": 27882280,
      "atraso_bytes": 62232,
      "wal_status": "reserved"
    },
    {
      "segundos": 16.529,
      "wal_retido_bytes": 27882280,
      "atraso_bytes": 62232,
      "wal_status": "reserved"
    },
    {
      "segundos": 16.729,
      "wal_retido_bytes": 27882280,
      "atraso_bytes": 62232,
      "wal_status": "reserved"
    },
    {
      "segundos": 16.93,
      "wal_retido_bytes": 27882280,
      "atraso_bytes": 62232,
      "wal_status": "reserved"
    },
    {
      "segundos": 17.131,
      "wal_retido_bytes": 27882280,
      "atraso_bytes": 62232,
      "wal_status": "reserved"
    },
    {
      "segundos": 17.332,
      "wal_retido_bytes": 27882280,
      "atraso_bytes": 62232,
      "wal_status": "reserved"
    },
    {
      "segundos": 17.532,
      "wal_retido_bytes": 30962472,
      "atraso_bytes": 3080192,
      "wal_status": "reserved"
    },
    {
      "segundos": 17.733,
      "wal_retido_bytes": 35156776,
      "atraso_bytes": 7274496,
      "wal_status": "reserved"
    },
    {
      "segundos": 17.934,
      "wal_retido_bytes": 39858984,
      "atraso_bytes": 11976704,
      "wal_status": "reserved"
    },
    {
      "segundos": 18.135,
      "wal_retido_bytes": 47739688,
      "atraso_bytes": 19857408,
      "wal_status": "reserved"
    },
    {
      "segundos": 18.335,
      "wal_retido_bytes": 51933992,
      "atraso_bytes": 24051712,
      "wal_status": "reserved"
    },
    {
      "segundos": 18.536,
      "wal_retido_bytes": 56128296,
      "atraso_bytes": 28246016,
      "wal_status": "reserved"
    },
    {
      "segundos": 18.737,
      "wal_retido_bytes": 64516904,
      "atraso_bytes": 36634624,
      "wal_status": "reserved"
    },
    {
      "segundos": 18.938,
      "wal_retido_bytes": 68711208,
      "atraso_bytes": 40828928,
      "wal_status": "reserved"
    },
    {
      "segundos": 19.138,
      "wal_retido_bytes": 72905512,
      "atraso_bytes": 45023232,
      "wal_status": "reserved"
    },
    {
      "segundos": 19.339,
      "wal_retido_bytes": 77042472,
      "atraso_bytes": 49160192,
      "wal_status": "reserved"
    },
    {
      "segundos": 19.54,
      "wal_retido_bytes": 82285352,
      "atraso_bytes": 54403072,
      "wal_status": "reserved"
    },
    {
      "segundos": 19.741,
      "wal_retido_bytes": 87225128,
      "atraso_bytes": 59342848,
      "wal_status": "reserved"
    },
    {
      "segundos": 19.941,
      "wal_retido_bytes": 92140328,
      "atraso_bytes": 64258048,
      "wal_status": "reserved"
    },
    {
      "segundos": 20.142,
      "wal_retido_bytes": 97162024,
      "atraso_bytes": 20062208,
      "wal_status": "reserved"
    },
    {
      "segundos": 20.402,
      "wal_retido_bytes": 103781160,
      "atraso_bytes": 26681344,
      "wal_status": "reserved"
    },
    {
      "segundos": 20.603,
      "wal_retido_bytes": 109056808,
      "atraso_bytes": 31956992,
      "wal_status": "reserved"
    },
    {
      "segundos": 20.804,
      "wal_retido_bytes": 114348840,
      "atraso_bytes": 37249024,
      "wal_status": "reserved"
    },
    {
      "segundos": 21.004,
      "wal_retido_bytes": 119526184,
      "atraso_bytes": 42426368,
      "wal_status": "reserved"
    },
    {
      "segundos": 21.205,
      "wal_retido_bytes": 124810024,
      "atraso_bytes": 47710208,
      "wal_status": "reserved"
    },
    {
      "segundos": 21.406,
      "wal_retido_bytes": 130036520,
      "atraso_bytes": 52936704,
      "wal_status": "reserved"
    },
    {
      "segundos": 21.607,
      "wal_retido_bytes": 135303976,
      "atraso_bytes": 58204160,
      "wal_status": "reserved"
    },
    {
      "segundos": 21.808,
      "wal_retido_bytes": 140571432,
      "atraso_bytes": 63471616,
      "wal_status": "reserved"
    },
    {
      "segundos": 22.009,
      "wal_retido_bytes": 145814312,
      "atraso_bytes": 68714496,
      "wal_status": "reserved"
    },
    {
      "segundos": 22.209,
      "wal_retido_bytes": 149582632,
      "atraso_bytes": 72482816,
      "wal_status": "reserved"
    },
    {
      "segundos": 22.41,
      "wal_retido_bytes": 162042664,
      "atraso_bytes": 84942848,
      "wal_status": "reserved"
    },
    {
      "segundos": 22.611,
      "wal_retido_bytes": 169374504,
      "atraso_bytes": 92274688,
      "wal_status": "reserved"
    },
    {
      "segundos": 22.812,
      "wal_retido_bytes": 177804072,
      "atraso_bytes": 33595392,
      "wal_status": "reserved"
    },
    {
      "segundos": 23.012,
      "wal_retido_bytes": 194138920,
      "atraso_bytes": 49930240,
      "wal_status": "reserved"
    },
    {
      "segundos": 23.213,
      "wal_retido_bytes": 210228008,
      "atraso_bytes": 66019328,
      "wal_status": "reserved"
    },
    {
      "segundos": 23.414,
      "wal_retido_bytes": 226939688,
      "atraso_bytes": 82731008,
      "wal_status": "reserved"
    },
    {
      "segundos": 23.615,
      "wal_retido_bytes": 243315496,
      "atraso_bytes": 99106816,
      "wal_status": "reserved"
    },
    {
      "segundos": 23.816,
      "wal_retido_bytes": 258724648,
      "atraso_bytes": 114515968,
      "wal_status": "reserved"
    },
    {
      "segundos": 24.016,
      "wal_retido_bytes": 274936616,
      "atraso_bytes": 130727936,
      "wal_status": "reserved"
    },
    {
      "segundos": 24.217,
      "wal_retido_bytes": 296645416,
      "atraso_bytes": 152436736,
      "wal_status": "reserved"
    },
    {
      "segundos": 24.418,
      "wal_retido_bytes": 321475368,
      "atraso_bytes": 177266688,
      "wal_status": "reserved"
    },
    {
      "segundos": 24.618,
      "wal_retido_bytes": 346936104,
      "atraso_bytes": 202727424,
      "wal_status": "reserved"
    },
    {
      "segundos": 24.819,
      "wal_retido_bytes": 373977896,
      "atraso_bytes": 229769216,
      "wal_status": "reserved"
    },
    {
      "segundos": 25.02,
      "wal_retido_bytes": 397431592,
      "atraso_bytes": 253222912,
      "wal_status": "reserved"
    },
    {
      "segundos": 25.221,
      "wal_retido_bytes": 421311272,
      "atraso_bytes": 277102592,
      "wal_status": "reserved"
    },
    {
      "segundos": 25.422,
      "wal_retido_bytes": 446165800,
      "atraso_bytes": 301948928,
      "wal_status": "reserved"
    },
    {
      "segundos": 25.622,
      "wal_retido_bytes": 470348584,
      "atraso_bytes": 326139904,
      "wal_status": "reserved"
    },
    {
      "segundos": 25.823,
      "wal_retido_bytes": 495342376,
      "atraso_bytes": 351133696,
      "wal_status": "reserved"
    },
    {
      "segundos": 26.024,
      "wal_retido_bytes": 516977448,
      "atraso_bytes": 372768768,
      "wal_status": "reserved"
    },
    {
      "segundos": 26.224,
      "wal_retido_bytes": 541840168,
      "atraso_bytes": 397631488,
      "wal_status": "reserved"
    },
    {
      "segundos": 26.425,
      "wal_retido_bytes": 564228904,
      "atraso_bytes": 420020224,
      "wal_status": "reserved"
    },
    {
      "segundos": 26.626,
      "wal_retido_bytes": 588321576,
      "atraso_bytes": 444104704,
      "wal_status": "reserved"
    },
    {
      "segundos": 26.827,
      "wal_retido_bytes": 612184872,
      "atraso_bytes": 467976192,
      "wal_status": "reserved"
    },
    {
      "segundos": 27.027,
      "wal_retido_bytes": 631542568,
      "atraso_bytes": 487333888,
      "wal_status": "reserved"
    },
    {
      "segundos": 27.228,
      "wal_retido_bytes": 634942248,
      "atraso_bytes": 490733568,
      "wal_status": "reserved"
    },
    {
      "segundos": 27.429,
      "wal_retido_bytes": 635728680,
      "atraso_bytes": 491520000,
      "wal_status": "reserved"
    },
    {
      "segundos": 27.63,
      "wal_retido_bytes": 635728680,
      "atraso_bytes": 491520000,
      "wal_status": "reserved"
    },
    {
      "segundos": 27.83,
      "wal_retido_bytes": 635728680,
      "atraso_bytes": 491520000,
      "wal_status": "reserved"
    },
    {
      "segundos": 28.031,
      "wal_retido_bytes": 635728680,
      "atraso_bytes": 273416192,
      "wal_status": "reserved"
    },
    {
      "segundos": 28.232,
      "wal_retido_bytes": 635728680,
      "atraso_bytes": 273416192,
      "wal_status": "reserved"
    },
    {
      "segundos": 28.433,
      "wal_retido_bytes": 635728680,
      "atraso_bytes": 273416192,
      "wal_status": "reserved"
    },
    {
      "segundos": 28.634,
      "wal_retido_bytes": 676262696,
      "atraso_bytes": 313950208,
      "wal_status": "reserved"
    },
    {
      "segundos": 28.834,
      "wal_retido_bytes": 734057256,
      "atraso_bytes": 371744768,
      "wal_status": "reserved"
    },
    {
      "segundos": 29.035,
      "wal_retido_bytes": 748188456,
      "atraso_bytes": 385875968,
      "wal_status": "reserved"
    },
    {
      "segundos": 29.236,
      "wal_retido_bytes": 752382760,
      "atraso_bytes": 390070272,
      "wal_status": "reserved"
    },
    {
      "segundos": 29.437,
      "wal_retido_bytes": 752399144,
      "atraso_bytes": 390086656,
      "wal_status": "reserved"
    },
    {
      "segundos": 29.637,
      "wal_retido_bytes": 752399144,
      "atraso_bytes": 390086656,
      "wal_status": "reserved"
    },
    {
      "segundos": 29.838,
      "wal_retido_bytes": 752403632,
      "atraso_bytes": 390091144,
      "wal_status": "reserved"
    },
    {
      "segundos": 30.039,
      "wal_retido_bytes": 752403632,
      "atraso_bytes": 390091144,
      "wal_status": "reserved"
    },
    {
      "segundos": 30.24,
      "wal_retido_bytes": 752403632,
      "atraso_bytes": 390091144,
      "wal_status": "reserved"
    },
    {
      "segundos": 30.614,
      "wal_retido_bytes": 752404736,
      "atraso_bytes": 390092248,
      "wal_status": "reserved"
    },
    {
      "segundos": 30.815,
      "wal_retido_bytes": 752404736,
      "atraso_bytes": 390092248,
      "wal_status": "reserved"
    },
    {
      "segundos": 31.016,
      "wal_retido_bytes": 752404736,
      "atraso_bytes": 390092248,
      "wal_status": "reserved"
    },
    {
      "segundos": 31.216,
      "wal_retido_bytes": 752404736,
      "atraso_bytes": 390092248,
      "wal_status": "reserved"
    },
    {
      "segundos": 31.417,
      "wal_retido_bytes": 752404736,
      "atraso_bytes": 390092248,
      "wal_status": "reserved"
    },
    {
      "segundos": 31.618,
      "wal_retido_bytes": 752404736,
      "atraso_bytes": 390092248,
      "wal_status": "reserved"
    },
    {
      "segundos": 31.819,
      "wal_retido_bytes": 752404736,
      "atraso_bytes": 390092248,
      "wal_status": "reserved"
    },
    {
      "segundos": 32.019,
      "wal_retido_bytes": 752404736,
      "atraso_bytes": 390092248,
      "wal_status": "reserved"
    },
    {
      "segundos": 32.22,
      "wal_retido_bytes": 752404736,
      "atraso_bytes": 390092248,
      "wal_status": "reserved"
    },
    {
      "segundos": 32.422,
      "wal_retido_bytes": 752404736,
      "atraso_bytes": 390092248,
      "wal_status": "reserved"
    },
    {
      "segundos": 32.623,
      "wal_retido_bytes": 752404736,
      "atraso_bytes": 390092248,
      "wal_status": "reserved"
    },
    {
      "segundos": 32.823,
      "wal_retido_bytes": 752841512,
      "atraso_bytes": 390529024,
      "wal_status": "reserved"
    },
    {
      "segundos": 33.024,
      "wal_retido_bytes": 804500264,
      "atraso_bytes": 442187776,
      "wal_status": "reserved"
    },
    {
      "segundos": 33.225,
      "wal_retido_bytes": 854283048,
      "atraso_bytes": 491962368,
      "wal_status": "reserved"
    },
    {
      "segundos": 33.426,
      "wal_retido_bytes": 908251944,
      "atraso_bytes": 545931264,
      "wal_status": "reserved"
    },
    {
      "segundos": 33.627,
      "wal_retido_bytes": 928576296,
      "atraso_bytes": 566263808,
      "wal_status": "reserved"
    },
    {
      "segundos": 33.828,
      "wal_retido_bytes": 932737832,
      "atraso_bytes": 570425344,
      "wal_status": "reserved"
    },
    {
      "segundos": 34.028,
      "wal_retido_bytes": 932762408,
      "atraso_bytes": 570449920,
      "wal_status": "reserved"
    },
    {
      "segundos": 34.229,
      "wal_retido_bytes": 932762408,
      "atraso_bytes": 570449920,
      "wal_status": "reserved"
    },
    {
      "segundos": 34.43,
      "wal_retido_bytes": 932762408,
      "atraso_bytes": 570449920,
      "wal_status": "reserved"
    },
    {
      "segundos": 34.631,
      "wal_retido_bytes": 951128872,
      "atraso_bytes": 588816384,
      "wal_status": "reserved"
    },
    {
      "segundos": 34.832,
      "wal_retido_bytes": 953709352,
      "atraso_bytes": 591396864,
      "wal_status": "reserved"
    },
    {
      "segundos": 35.033,
      "wal_retido_bytes": 955757352,
      "atraso_bytes": 593444864,
      "wal_status": "reserved"
    },
    {
      "segundos": 35.233,
      "wal_retido_bytes": 956969768,
      "atraso_bytes": 594657280,
      "wal_status": "reserved"
    },
    {
      "segundos": 35.434,
      "wal_retido_bytes": 960820008,
      "atraso_bytes": 598507520,
      "wal_status": "reserved"
    },
    {
      "segundos": 35.635,
      "wal_retido_bytes": 964752168,
      "atraso_bytes": 602439680,
      "wal_status": "reserved"
    },
    {
      "segundos": 35.836,
      "wal_retido_bytes": 970823400,
      "atraso_bytes": 608510912,
      "wal_status": "reserved"
    },
    {
      "segundos": 36.036,
      "wal_retido_bytes": 972960552,
      "atraso_bytes": 610648064,
      "wal_status": "reserved"
    },
    {
      "segundos": 36.238,
      "wal_retido_bytes": 975082280,
      "atraso_bytes": 612769792,
      "wal_status": "reserved"
    },
    {
      "segundos": 36.439,
      "wal_retido_bytes": 984192504,
      "atraso_bytes": 231788872,
      "wal_status": "reserved"
    },
    {
      "segundos": 36.64,
      "wal_retido_bytes": 985338664,
      "atraso_bytes": 232935032,
      "wal_status": "reserved"
    },
    {
      "segundos": 36.841,
      "wal_retido_bytes": 985355048,
      "atraso_bytes": 232951416,
      "wal_status": "reserved"
    },
    {
      "segundos": 37.042,
      "wal_retido_bytes": 985355048,
      "atraso_bytes": 232951416,
      "wal_status": "reserved"
    },
    {
      "segundos": 37.243,
      "wal_retido_bytes": 985355048,
      "atraso_bytes": 232951416,
      "wal_status": "reserved"
    },
    {
      "segundos": 37.444,
      "wal_retido_bytes": 985371432,
      "atraso_bytes": 232967800,
      "wal_status": "reserved"
    },
    {
      "segundos": 37.645,
      "wal_retido_bytes": 985449176,
      "atraso_bytes": 233045544,
      "wal_status": "reserved"
    },
    {
      "segundos": 37.846,
      "wal_retido_bytes": 985449176,
      "atraso_bytes": 233045544,
      "wal_status": "reserved"
    },
    {
      "segundos": 38.047,
      "wal_retido_bytes": 985449176,
      "atraso_bytes": 233045544,
      "wal_status": "reserved"
    },
    {
      "segundos": 38.248,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 38.449,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 38.649,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 38.85,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 39.051,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 39.252,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 39.452,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 39.653,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 39.854,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 40.055,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 40.255,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 40.456,
      "wal_retido_bytes": 985449352,
      "atraso_bytes": 233045720,
      "wal_status": "reserved"
    },
    {
      "segundos": 40.663,
      "wal_retido_bytes": 985449528,
      "atraso_bytes": 233045896,
      "wal_status": "reserved"
    },
    {
      "segundos": 40.864,
      "wal_retido_bytes": 985449528,
      "atraso_bytes": 233045896,
      "wal_status": "reserved"
    },
    {
      "segundos": 41.064,
      "wal_retido_bytes": 985449528,
      "atraso_bytes": 233045896,
      "wal_status": "reserved"
    },
    {
      "segundos": 41.265,
      "wal_retido_bytes": 985449528,
      "atraso_bytes": 233045896,
      "wal_status": "reserved"
    },
    {
      "segundos": 41.466,
      "wal_retido_bytes": 985449528,
      "atraso_bytes": 233045896,
      "wal_status": "reserved"
    },
    {
      "segundos": 41.666,
      "wal_retido_bytes": 985449528,
      "atraso_bytes": 233045896,
      "wal_status": "reserved"
    },
    {
      "segundos": 41.867,
      "wal_retido_bytes": 985449528,
      "atraso_bytes": 233045896,
      "wal_status": "reserved"
    },
    {
      "segundos": 42.068,
      "wal_retido_bytes": 985449528,
      "atraso_bytes": 233045896,
      "wal_status": "reserved"
    }
  ]
}
```

</details>

### dbt

<details markdown="1">
<summary>Resumo e 555 amostras coletadas</summary>

```json
{
  "data_utc": "2026-11-08T15:57:42.729539+00:00",
  "comando": [
    "/home/pedro/Obsidian/Semestre-2026-2/BD2/04-Projeto/bd2_2026_2_eduinfra/.venv/bin/python",
    "/tmp/eduinfra-issue48-60-integration/transformacao/scripts/executar.py"
  ],
  "escolas": 181065,
  "itens_antes": 3078581,
  "limite_slot": "2GB",
  "intervalo_consumidor_s": 2,
  "checkpoint_s": 10,
  "duracao_construcao_e_insercao_s": 109.03,
  "wal_gerado_bytes": 1868134216,
  "pico_retido_bytes": 1196987160,
  "retido_final_bytes": 17752,
  "wal_status_final": "reserved",
  "estados_observados": [
    "extended",
    "reserved"
  ],
  "passadas": 17,
  "itens_do_marcador": 17,
  "chaves_do_marcador": 17,
  "itens_esperados_do_marcador": 17,
  "erros": [],
  "amostras": [
    {
      "segundos": 0.011,
      "wal_retido_bytes": 120,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 0.212,
      "wal_retido_bytes": 120,
      "atraso_bytes": 0,
      "wal_status": "reserved"
    },
    {
      "segundos": 0.414,
      "wal_retido_bytes": 98520,
      "atraso_bytes": 98400,
      "wal_status": "reserved"
    },
    {
      "segundos": 0.615,
      "wal_retido_bytes": 349456,
      "atraso_bytes": 349336,
      "wal_status": "reserved"
    },
    {
      "segundos": 0.816,
      "wal_retido_bytes": 9123088,
      "atraso_bytes": 9122968,
      "wal_status": "reserved"
    },
    {
      "segundos": 1.02,
      "wal_retido_bytes": 17896720,
      "atraso_bytes": 17896600,
      "wal_status": "reserved"
    },
    {
      "segundos": 1.222,
      "wal_retido_bytes": 22541584,
      "atraso_bytes": 22541464,
      "wal_status": "reserved"
    },
    {
      "segundos": 1.424,
      "wal_retido_bytes": 23786768,
      "atraso_bytes": 23786648,
      "wal_status": "reserved"
    },
    {
      "segundos": 1.625,
      "wal_retido_bytes": 24794384,
      "atraso_bytes": 24794264,
      "wal_status": "reserved"
    },
    {
      "segundos": 1.826,
      "wal_retido_bytes": 24794384,
      "atraso_bytes": 24794264,
      "wal_status": "reserved"
    },
    {
      "segundos": 2.027,
      "wal_retido_bytes": 29660432,
      "atraso_bytes": 29660312,
      "wal_status": "reserved"
    },
    {
      "segundos": 2.228,
      "wal_retido_bytes": 32175376,
      "atraso_bytes": 9764776,
      "wal_status": "reserved"
    },
    {
      "segundos": 2.428,
      "wal_retido_bytes": 35009808,
      "atraso_bytes": 12599208,
      "wal_status": "reserved"
    },
    {
      "segundos": 2.629,
      "wal_retido_bytes": 35763472,
      "atraso_bytes": 13352872,
      "wal_status": "reserved"
    },
    {
      "segundos": 2.83,
      "wal_retido_bytes": 40932624,
      "atraso_bytes": 18522024,
      "wal_status": "reserved"
    },
    {
      "segundos": 3.031,
      "wal_retido_bytes": 44758288,
      "atraso_bytes": 22347688,
      "wal_status": "reserved"
    },
    {
      "segundos": 3.232,
      "wal_retido_bytes": 45118736,
      "atraso_bytes": 22708136,
      "wal_status": "reserved"
    },
    {
      "segundos": 3.432,
      "wal_retido_bytes": 51115280,
      "atraso_bytes": 28704680,
      "wal_status": "reserved"
    },
    {
      "segundos": 3.633,
      "wal_retido_bytes": 53146896,
      "atraso_bytes": 30736296,
      "wal_status": "reserved"
    },
    {
      "segundos": 3.834,
      "wal_retido_bytes": 55301392,
      "atraso_bytes": 32890792,
      "wal_status": "reserved"
    },
    {
      "segundos": 4.035,
      "wal_retido_bytes": 61306128,
      "atraso_bytes": 38895528,
      "wal_status": "reserved"
    },
    {
      "segundos": 4.236,
      "wal_retido_bytes": 61535504,
      "atraso_bytes": 39124904,
      "wal_status": "reserved"
    },
    {
      "segundos": 4.436,
      "wal_retido_bytes": 65492240,
      "atraso_bytes": 43081640,
      "wal_status": "reserved"
    },
    {
      "segundos": 4.637,
      "wal_retido_bytes": 71488784,
      "atraso_bytes": 49078184,
      "wal_status": "reserved"
    },
    {
      "segundos": 4.838,
      "wal_retido_bytes": 74118416,
      "atraso_bytes": 12582912,
      "wal_status": "reserved"
    },
    {
      "segundos": 5.039,
      "wal_retido_bytes": 75674896,
      "atraso_bytes": 14139392,
      "wal_status": "reserved"
    },
    {
      "segundos": 5.239,
      "wal_retido_bytes": 81622288,
      "atraso_bytes": 20086784,
      "wal_status": "reserved"
    },
    {
      "segundos": 5.44,
      "wal_retido_bytes": 82507024,
      "atraso_bytes": 20971520,
      "wal_status": "reserved"
    },
    {
      "segundos": 5.641,
      "wal_retido_bytes": 85816592,
      "atraso_bytes": 24281088,
      "wal_status": "reserved"
    },
    {
      "segundos": 5.842,
      "wal_retido_bytes": 91845904,
      "atraso_bytes": 30310400,
      "wal_status": "reserved"
    },
    {
      "segundos": 6.043,
      "wal_retido_bytes": 95089936,
      "atraso_bytes": 33554432,
      "wal_status": "reserved"
    },
    {
      "segundos": 6.243,
      "wal_retido_bytes": 95089936,
      "atraso_bytes": 33554432,
      "wal_status": "reserved"
    },
    {
      "segundos": 6.444,
      "wal_retido_bytes": 96032016,
      "atraso_bytes": 34496512,
      "wal_status": "reserved"
    },
    {
      "segundos": 6.645,
      "wal_retido_bytes": 101864720,
      "atraso_bytes": 40329216,
      "wal_status": "reserved"
    },
    {
      "segundos": 6.845,
      "wal_retido_bytes": 103478544,
      "atraso_bytes": 41943040,
      "wal_status": "reserved"
    },
    {
      "segundos": 7.046,
      "wal_retido_bytes": 111572240,
      "atraso_bytes": 50036736,
      "wal_status": "reserved"
    },
    {
      "segundos": 7.247,
      "wal_retido_bytes": 116061456,
      "atraso_bytes": 54525952,
      "wal_status": "reserved"
    },
    {
      "segundos": 7.448,
      "wal_retido_bytes": 116143376,
      "atraso_bytes": 54607872,
      "wal_status": "reserved"
    },
    {
      "segundos": 7.649,
      "wal_retido_bytes": 120247568,
      "atraso_bytes": 16769024,
      "wal_status": "reserved"
    },
    {
      "segundos": 7.849,
      "wal_retido_bytes": 124450064,
      "atraso_bytes": 20971520,
      "wal_status": "reserved"
    },
    {
      "segundos": 8.05,
      "wal_retido_bytes": 126072080,
      "atraso_bytes": 22593536,
      "wal_status": "reserved"
    },
    {
      "segundos": 8.251,
      "wal_retido_bytes": 129004816,
      "atraso_bytes": 25526272,
      "wal_status": "reserved"
    },
    {
      "segundos": 8.451,
      "wal_retido_bytes": 132838672,
      "atraso_bytes": 29360128,
      "wal_status": "reserved"
    },
    {
      "segundos": 8.652,
      "wal_retido_bytes": 135902480,
      "atraso_bytes": 32423936,
      "wal_status": "reserved"
    },
    {
      "segundos": 8.853,
      "wal_retido_bytes": 138188048,
      "atraso_bytes": 34709504,
      "wal_status": "reserved"
    },
    {
      "segundos": 9.054,
      "wal_retido_bytes": 145421584,
      "atraso_bytes": 41943040,
      "wal_status": "reserved"
    },
    {
      "segundos": 9.255,
      "wal_retido_bytes": 145536272,
      "atraso_bytes": 42057728,
      "wal_status": "reserved"
    },
    {
      "segundos": 9.455,
      "wal_retido_bytes": 148673808,
      "atraso_bytes": 45195264,
      "wal_status": "reserved"
    },
    {
      "segundos": 9.656,
      "wal_retido_bytes": 153810192,
      "atraso_bytes": 50331648,
      "wal_status": "reserved"
    },
    {
      "segundos": 9.857,
      "wal_retido_bytes": 155464976,
      "atraso_bytes": 51986432,
      "wal_status": "reserved"
    },
    {
      "segundos": 10.058,
      "wal_retido_bytes": 158053648,
      "atraso_bytes": 54575104,
      "wal_status": "reserved"
    },
    {
      "segundos": 10.4,
      "wal_retido_bytes": 165633736,
      "atraso_bytes": 62155192,
      "wal_status": "reserved"
    },
    {
      "segundos": 10.601,
      "wal_retido_bytes": 166393104,
      "atraso_bytes": 62914560,
      "wal_status": "reserved"
    },
    {
      "segundos": 10.802,
      "wal_retido_bytes": 174781712,
      "atraso_bytes": 28819456,
      "wal_status": "reserved"
    },
    {
      "segundos": 11.003,
      "wal_retido_bytes": 176256272,
      "atraso_bytes": 30294016,
      "wal_status": "reserved"
    },
    {
      "segundos": 11.203,
      "wal_retido_bytes": 176534800,
      "atraso_bytes": 30572544,
      "wal_status": "reserved"
    },
    {
      "segundos": 11.404,
      "wal_retido_bytes": 183170320,
      "atraso_bytes": 37208064,
      "wal_status": "reserved"
    },
    {
      "segundos": 11.605,
      "wal_retido_bytes": 186586384,
      "atraso_bytes": 40624128,
      "wal_status": "reserved"
    },
    {
      "segundos": 11.805,
      "wal_retido_bytes": 186592080,
      "atraso_bytes": 40629824,
      "wal_status": "reserved"
    },
    {
      "segundos": 12.006,
      "wal_retido_bytes": 195753232,
      "atraso_bytes": 49790976,
      "wal_status": "reserved"
    },
    {
      "segundos": 12.207,
      "wal_retido_bytes": 196711696,
      "atraso_bytes": 50749440,
      "wal_status": "reserved"
    },
    {
      "segundos": 12.408,
      "wal_retido_bytes": 197063952,
      "atraso_bytes": 51101696,
      "wal_status": "reserved"
    },
    {
      "segundos": 12.608,
      "wal_retido_bytes": 204141840,
      "atraso_bytes": 58179584,
      "wal_status": "reserved"
    },
    {
      "segundos": 12.809,
      "wal_retido_bytes": 207156496,
      "atraso_bytes": 61194240,
      "wal_status": "reserved"
    },
    {
      "segundos": 13.01,
      "wal_retido_bytes": 207205648,
      "atraso_bytes": 61243392,
      "wal_status": "reserved"
    },
    {
      "segundos": 13.211,
      "wal_retido_bytes": 212530448,
      "atraso_bytes": 66568192,
      "wal_status": "reserved"
    },
    {
      "segundos": 13.411,
      "wal_retido_bytes": 216724752,
      "atraso_bytes": 70762496,
      "wal_status": "reserved"
    },
    {
      "segundos": 13.612,
      "wal_retido_bytes": 217732368,
      "atraso_bytes": 71770112,
      "wal_status": "reserved"
    },
    {
      "segundos": 13.812,
      "wal_retido_bytes": 220919056,
      "atraso_bytes": 74956800,
      "wal_status": "reserved"
    },
    {
      "segundos": 14.013,
      "wal_retido_bytes": 225113360,
      "atraso_bytes": 79151104,
      "wal_status": "reserved"
    },
    {
      "segundos": 14.214,
      "wal_retido_bytes": 227915024,
      "atraso_bytes": 81952768,
      "wal_status": "reserved"
    },
    {
      "segundos": 14.414,
      "wal_retido_bytes": 227915024,
      "atraso_bytes": 81952768,
      "wal_status": "reserved"
    },
    {
      "segundos": 14.615,
      "wal_retido_bytes": 233641232,
      "atraso_bytes": 87678976,
      "wal_status": "reserved"
    },
    {
      "segundos": 14.816,
      "wal_retido_bytes": 237696272,
      "atraso_bytes": 33554432,
      "wal_status": "reserved"
    },
    {
      "segundos": 15.016,
      "wal_retido_bytes": 237876496,
      "atraso_bytes": 33734656,
      "wal_status": "reserved"
    },
    {
      "segundos": 15.217,
      "wal_retido_bytes": 243914000,
      "atraso_bytes": 39772160,
      "wal_status": "reserved"
    },
    {
      "segundos": 15.418,
      "wal_retido_bytes": 246084880,
      "atraso_bytes": 41943040,
      "wal_status": "reserved"
    },
    {
      "segundos": 15.619,
      "wal_retido_bytes": 248157456,
      "atraso_bytes": 44015616,
      "wal_status": "reserved"
    },
    {
      "segundos": 15.819,
      "wal_retido_bytes": 253842704,
      "atraso_bytes": 49700864,
      "wal_status": "reserved"
    },
    {
      "segundos": 16.02,
      "wal_retido_bytes": 254473488,
      "atraso_bytes": 50331648,
      "wal_status": "reserved"
    },
    {
      "segundos": 16.221,
      "wal_retido_bytes": 258561296,
      "atraso_bytes": 54419456,
      "wal_status": "reserved"
    },
    {
      "segundos": 16.422,
      "wal_retido_bytes": 263582992,
      "atraso_bytes": 59441152,
      "wal_status": "reserved"
    },
    {
      "segundos": 16.622,
      "wal_retido_bytes": 267056400,
      "atraso_bytes": 62914560,
      "wal_status": "reserved"
    },
    {
      "segundos": 16.823,
      "wal_retido_bytes": 268866832,
      "atraso_bytes": 64724992,
      "wal_status": "reserved"
    },
    {
      "segundos": 17.024,
      "wal_retido_bytes": 269571344,
      "atraso_bytes": 65429504,
      "wal_status": "reserved"
    },
    {
      "segundos": 17.225,
      "wal_retido_bytes": 275428624,
      "atraso_bytes": 71286784,
      "wal_status": "reserved"
    },
    {
      "segundos": 17.426,
      "wal_retido_bytes": 275445008,
      "atraso_bytes": 71303168,
      "wal_status": "reserved"
    },
    {
      "segundos": 17.627,
      "wal_retido_bytes": 279106832,
      "atraso_bytes": 74964992,
      "wal_status": "reserved"
    },
    {
      "segundos": 17.828,
      "wal_retido_bytes": 284538128,
      "atraso_bytes": 80396288,
      "wal_status": "reserved"
    },
    {
      "segundos": 18.029,
      "wal_retido_bytes": 288027920,
      "atraso_bytes": 83886080,
      "wal_status": "reserved"
    },
    {
      "segundos": 18.23,
      "wal_retido_bytes": 289182992,
      "atraso_bytes": 85041152,
      "wal_status": "reserved"
    },
    {
      "segundos": 18.43,
      "wal_retido_bytes": 290706704,
      "atraso_bytes": 86564864,
      "wal_status": "reserved"
    },
    {
      "segundos": 18.631,
      "wal_retido_bytes": 298941856,
      "atraso_bytes": 94800016,
      "wal_status": "reserved"
    },
    {
      "segundos": 18.832,
      "wal_retido_bytes": 299406608,
      "atraso_bytes": 95264768,
      "wal_status": "reserved"
    },
    {
      "segundos": 19.033,
      "wal_retido_bytes": 299903680,
      "atraso_bytes": 95761840,
      "wal_status": "reserved"
    },
    {
      "segundos": 19.233,
      "wal_retido_bytes": 305887328,
      "atraso_bytes": 101745488,
      "wal_status": "reserved"
    },
    {
      "segundos": 19.434,
      "wal_retido_bytes": 308999440,
      "atraso_bytes": 40132608,
      "wal_status": "reserved"
    },
    {
      "segundos": 19.635,
      "wal_retido_bytes": 309425424,
      "atraso_bytes": 40558592,
      "wal_status": "reserved"
    },
    {
      "segundos": 19.836,
      "wal_retido_bytes": 309425424,
      "atraso_bytes": 40558592,
      "wal_status": "reserved"
    },
    {
      "segundos": 20.037,
      "wal_retido_bytes": 315471120,
      "atraso_bytes": 46604288,
      "wal_status": "reserved"
    },
    {
      "segundos": 20.237,
      "wal_retido_bytes": 317388048,
      "atraso_bytes": 48521216,
      "wal_status": "reserved"
    },
    {
      "segundos": 20.586,
      "wal_retido_bytes": 320648464,
      "atraso_bytes": 51781632,
      "wal_status": "reserved"
    },
    {
      "segundos": 20.787,
      "wal_retido_bytes": 330080496,
      "atraso_bytes": 61213664,
      "wal_status": "reserved"
    },
    {
      "segundos": 20.988,
      "wal_retido_bytes": 330396944,
      "atraso_bytes": 61530112,
      "wal_status": "reserved"
    },
    {
      "segundos": 21.189,
      "wal_retido_bytes": 330396944,
      "atraso_bytes": 61530112,
      "wal_status": "reserved"
    },
    {
      "segundos": 21.389,
      "wal_retido_bytes": 338249744,
      "atraso_bytes": 69382912,
      "wal_status": "reserved"
    },
    {
      "segundos": 21.59,
      "wal_retido_bytes": 338359568,
      "atraso_bytes": 69492736,
      "wal_status": "reserved"
    },
    {
      "segundos": 21.791,
      "wal_retido_bytes": 340808976,
      "atraso_bytes": 71942144,
      "wal_status": "reserved"
    },
    {
      "segundos": 21.992,
      "wal_retido_bytes": 340808976,
      "atraso_bytes": 71942144,
      "wal_status": "reserved"
    },
    {
      "segundos": 22.193,
      "wal_retido_bytes": 347362576,
      "atraso_bytes": 78495744,
      "wal_status": "reserved"
    },
    {
      "segundos": 22.394,
      "wal_retido_bytes": 350942480,
      "atraso_bytes": 82075648,
      "wal_status": "reserved"
    },
    {
      "segundos": 22.594,
      "wal_retido_bytes": 351548688,
      "atraso_bytes": 82681856,
      "wal_status": "reserved"
    },
    {
      "segundos": 22.795,
      "wal_retido_bytes": 355136784,
      "atraso_bytes": 86269952,
      "wal_status": "reserved"
    },
    {
      "segundos": 22.996,
      "wal_retido_bytes": 359331088,
      "atraso_bytes": 90464256,
      "wal_status": "reserved"
    },
    {
      "segundos": 23.197,
      "wal_retido_bytes": 362034448,
      "atraso_bytes": 93167616,
      "wal_status": "reserved"
    },
    {
      "segundos": 23.397,
      "wal_retido_bytes": 364121384,
      "atraso_bytes": 95254552,
      "wal_status": "reserved"
    },
    {
      "segundos": 23.598,
      "wal_retido_bytes": 370947344,
      "atraso_bytes": 102080512,
      "wal_status": "reserved"
    },
    {
      "segundos": 23.799,
      "wal_retido_bytes": 371914000,
      "atraso_bytes": 103047168,
      "wal_status": "reserved"
    },
    {
      "segundos": 23.999,
      "wal_retido_bytes": 372913424,
      "atraso_bytes": 104046592,
      "wal_status": "reserved"
    },
    {
      "segundos": 24.2,
      "wal_retido_bytes": 376108304,
      "atraso_bytes": 107241472,
      "wal_status": "reserved"
    },
    {
      "segundos": 24.401,
      "wal_retido_bytes": 380302608,
      "atraso_bytes": 111435776,
      "wal_status": "reserved"
    },
    {
      "segundos": 24.602,
      "wal_retido_bytes": 383505680,
      "atraso_bytes": 114638848,
      "wal_status": "reserved"
    },
    {
      "segundos": 24.803,
      "wal_retido_bytes": 383505680,
      "atraso_bytes": 114638848,
      "wal_status": "reserved"
    },
    {
      "segundos": 25.003,
      "wal_retido_bytes": 388691216,
      "atraso_bytes": 50441472,
      "wal_status": "reserved"
    },
    {
      "segundos": 25.204,
      "wal_retido_bytes": 392885520,
      "atraso_bytes": 54635776,
      "wal_status": "reserved"
    },
    {
      "segundos": 25.405,
      "wal_retido_bytes": 394188048,
      "atraso_bytes": 55938304,
      "wal_status": "reserved"
    },
    {
      "segundos": 25.606,
      "wal_retido_bytes": 397338040,
      "atraso_bytes": 59088296,
      "wal_status": "reserved"
    },
    {
      "segundos": 25.806,
      "wal_retido_bytes": 404714768,
      "atraso_bytes": 66465024,
      "wal_status": "reserved"
    },
    {
      "segundos": 26.007,
      "wal_retido_bytes": 404714768,
      "atraso_bytes": 66465024,
      "wal_status": "reserved"
    },
    {
      "segundos": 26.208,
      "wal_retido_bytes": 404714768,
      "atraso_bytes": 66465024,
      "wal_status": "reserved"
    },
    {
      "segundos": 26.408,
      "wal_retido_bytes": 409400592,
      "atraso_bytes": 71150848,
      "wal_status": "reserved"
    },
    {
      "segundos": 26.609,
      "wal_retido_bytes": 415184144,
      "atraso_bytes": 76934400,
      "wal_status": "reserved"
    },
    {
      "segundos": 26.81,
      "wal_retido_bytes": 415186816,
      "atraso_bytes": 76937072,
      "wal_status": "reserved"
    },
    {
      "segundos": 27.011,
      "wal_retido_bytes": 415186816,
      "atraso_bytes": 76937072,
      "wal_status": "reserved"
    },
    {
      "segundos": 27.212,
      "wal_retido_bytes": 418944272,
      "atraso_bytes": 80694528,
      "wal_status": "reserved"
    },
    {
      "segundos": 27.413,
      "wal_retido_bytes": 422245648,
      "atraso_bytes": 83995904,
      "wal_status": "reserved"
    },
    {
      "segundos": 27.613,
      "wal_retido_bytes": 425538832,
      "atraso_bytes": 87289088,
      "wal_status": "reserved"
    },
    {
      "segundos": 27.814,
      "wal_retido_bytes": 425538832,
      "atraso_bytes": 87289088,
      "wal_status": "reserved"
    },
    {
      "segundos": 28.015,
      "wal_retido_bytes": 433916048,
      "atraso_bytes": 95666304,
      "wal_status": "reserved"
    },
    {
      "segundos": 28.215,
      "wal_retido_bytes": 434353424,
      "atraso_bytes": 96103680,
      "wal_status": "reserved"
    },
    {
      "segundos": 28.416,
      "wal_retido_bytes": 434828560,
      "atraso_bytes": 96578816,
      "wal_status": "reserved"
    },
    {
      "segundos": 28.617,
      "wal_retido_bytes": 436090128,
      "atraso_bytes": 97840384,
      "wal_status": "reserved"
    },
    {
      "segundos": 28.818,
      "wal_retido_bytes": 442266896,
      "atraso_bytes": 104017152,
      "wal_status": "reserved"
    },
    {
      "segundos": 29.019,
      "wal_retido_bytes": 443217168,
      "atraso_bytes": 104967424,
      "wal_status": "reserved"
    },
    {
      "segundos": 29.219,
      "wal_retido_bytes": 446453008,
      "atraso_bytes": 108203264,
      "wal_status": "reserved"
    },
    {
      "segundos": 29.42,
      "wal_retido_bytes": 446453008,
      "atraso_bytes": 108203264,
      "wal_status": "reserved"
    },
    {
      "segundos": 29.621,
      "wal_retido_bytes": 455134688,
      "atraso_bytes": 116884944,
      "wal_status": "reserved"
    },
    {
      "segundos": 29.822,
      "wal_retido_bytes": 455800080,
      "atraso_bytes": 117550336,
      "wal_status": "reserved"
    },
    {
      "segundos": 30.022,
      "wal_retido_bytes": 456488208,
      "atraso_bytes": 118238464,
      "wal_status": "reserved"
    },
    {
      "segundos": 30.223,
      "wal_retido_bytes": 462201520,
      "atraso_bytes": 123951776,
      "wal_status": "reserved"
    },
    {
      "segundos": 30.424,
      "wal_retido_bytes": 466450744,
      "atraso_bytes": 128201000,
      "wal_status": "reserved"
    },
    {
      "segundos": 30.72,
      "wal_retido_bytes": 467056672,
      "atraso_bytes": 128806928,
      "wal_status": "reserved"
    },
    {
      "segundos": 30.92,
      "wal_retido_bytes": 468382992,
      "atraso_bytes": 130133248,
      "wal_status": "reserved"
    },
    {
      "segundos": 31.121,
      "wal_retido_bytes": 476755216,
      "atraso_bytes": 61568400,
      "wal_status": "reserved"
    },
    {
      "segundos": 31.322,
      "wal_retido_bytes": 476771600,
      "atraso_bytes": 61584784,
      "wal_status": "reserved"
    },
    {
      "segundos": 31.522,
      "wal_retido_bytes": 478033168,
      "atraso_bytes": 62846352,
      "wal_status": "reserved"
    },
    {
      "segundos": 31.723,
      "wal_retido_bytes": 480965904,
      "atraso_bytes": 65779088,
      "wal_status": "reserved"
    },
    {
      "segundos": 31.924,
      "wal_retido_bytes": 485160208,
      "atraso_bytes": 69973392,
      "wal_status": "reserved"
    },
    {
      "segundos": 32.124,
      "wal_retido_bytes": 488633616,
      "atraso_bytes": 73446800,
      "wal_status": "reserved"
    },
    {
      "segundos": 32.325,
      "wal_retido_bytes": 488633616,
      "atraso_bytes": 73446800,
      "wal_status": "reserved"
    },
    {
      "segundos": 32.526,
      "wal_retido_bytes": 497767280,
      "atraso_bytes": 82580464,
      "wal_status": "reserved"
    },
    {
      "segundos": 32.726,
      "wal_retido_bytes": 499266832,
      "atraso_bytes": 84080016,
      "wal_status": "reserved"
    },
    {
      "segundos": 32.927,
      "wal_retido_bytes": 499266832,
      "atraso_bytes": 84080016,
      "wal_status": "reserved"
    },
    {
      "segundos": 33.128,
      "wal_retido_bytes": 501658896,
      "atraso_bytes": 86472080,
      "wal_status": "reserved"
    },
    {
      "segundos": 33.328,
      "wal_retido_bytes": 506131728,
      "atraso_bytes": 90944912,
      "wal_status": "reserved"
    },
    {
      "segundos": 33.529,
      "wal_retido_bytes": 509973776,
      "atraso_bytes": 94786960,
      "wal_status": "reserved"
    },
    {
      "segundos": 33.73,
      "wal_retido_bytes": 509973776,
      "atraso_bytes": 94786960,
      "wal_status": "reserved"
    },
    {
      "segundos": 33.93,
      "wal_retido_bytes": 514520336,
      "atraso_bytes": 99333520,
      "wal_status": "reserved"
    },
    {
      "segundos": 34.131,
      "wal_retido_bytes": 518714640,
      "atraso_bytes": 103527824,
      "wal_status": "reserved"
    },
    {
      "segundos": 34.332,
      "wal_retido_bytes": 520647952,
      "atraso_bytes": 105461136,
      "wal_status": "reserved"
    },
    {
      "segundos": 34.533,
      "wal_retido_bytes": 520647952,
      "atraso_bytes": 105461136,
      "wal_status": "reserved"
    },
    {
      "segundos": 34.733,
      "wal_retido_bytes": 528577808,
      "atraso_bytes": 113390992,
      "wal_status": "reserved"
    },
    {
      "segundos": 34.934,
      "wal_retido_bytes": 530864040,
      "atraso_bytes": 115677224,
      "wal_status": "reserved"
    },
    {
      "segundos": 35.135,
      "wal_retido_bytes": 531174672,
      "atraso_bytes": 115987856,
      "wal_status": "reserved"
    },
    {
      "segundos": 35.335,
      "wal_retido_bytes": 531174672,
      "atraso_bytes": 115987856,
      "wal_status": "reserved"
    },
    {
      "segundos": 35.536,
      "wal_retido_bytes": 537893040,
      "atraso_bytes": 122706224,
      "wal_status": "reserved"
    },
    {
      "segundos": 35.737,
      "wal_retido_bytes": 539686160,
      "atraso_bytes": 124499344,
      "wal_status": "reserved"
    },
    {
      "segundos": 35.938,
      "wal_retido_bytes": 541398288,
      "atraso_bytes": 126211472,
      "wal_status": "reserved"
    },
    {
      "segundos": 36.138,
      "wal_retido_bytes": 541398288,
      "atraso_bytes": 126211472,
      "wal_status": "reserved"
    },
    {
      "segundos": 36.339,
      "wal_retido_bytes": 547714320,
      "atraso_bytes": 132527504,
      "wal_status": "reserved"
    },
    {
      "segundos": 36.54,
      "wal_retido_bytes": 548074768,
      "atraso_bytes": 132887952,
      "wal_status": "reserved"
    },
    {
      "segundos": 36.741,
      "wal_retido_bytes": 551900432,
      "atraso_bytes": 136713616,
      "wal_status": "reserved"
    },
    {
      "segundos": 36.941,
      "wal_retido_bytes": 552908048,
      "atraso_bytes": 137721232,
      "wal_status": "reserved"
    },
    {
      "segundos": 37.142,
      "wal_retido_bytes": 552908048,
      "atraso_bytes": 137721232,
      "wal_status": "reserved"
    },
    {
      "segundos": 37.343,
      "wal_retido_bytes": 562416872,
      "atraso_bytes": 147230056,
      "wal_status": "reserved"
    },
    {
      "segundos": 37.544,
      "wal_retido_bytes": 562533648,
      "atraso_bytes": 147346832,
      "wal_status": "reserved"
    },
    {
      "segundos": 37.744,
      "wal_retido_bytes": 570601496,
      "atraso_bytes": 155414680,
      "wal_status": "reserved"
    },
    {
      "segundos": 37.945,
      "wal_retido_bytes": 573134096,
      "atraso_bytes": 157947280,
      "wal_status": "reserved"
    },
    {
      "segundos": 38.146,
      "wal_retido_bytes": 573134096,
      "atraso_bytes": 157947280,
      "wal_status": "reserved"
    },
    {
      "segundos": 38.346,
      "wal_retido_bytes": 573134096,
      "atraso_bytes": 71475200,
      "wal_status": "reserved"
    },
    {
      "segundos": 38.547,
      "wal_retido_bytes": 580007184,
      "atraso_bytes": 78348288,
      "wal_status": "reserved"
    },
    {
      "segundos": 38.748,
      "wal_retido_bytes": 581629200,
      "atraso_bytes": 79970304,
      "wal_status": "reserved"
    },
    {
      "segundos": 38.949,
      "wal_retido_bytes": 584193296,
      "atraso_bytes": 82534400,
      "wal_status": "reserved"
    },
    {
      "segundos": 39.15,
      "wal_retido_bytes": 586601744,
      "atraso_bytes": 84942848,
      "wal_status": "reserved"
    },
    {
      "segundos": 39.351,
      "wal_retido_bytes": 594120136,
      "atraso_bytes": 92461240,
      "wal_status": "reserved"
    },
    {
      "segundos": 39.552,
      "wal_retido_bytes": 594212112,
      "atraso_bytes": 92553216,
      "wal_status": "reserved"
    },
    {
      "segundos": 39.752,
      "wal_retido_bytes": 595135976,
      "atraso_bytes": 93477080,
      "wal_status": "reserved"
    },
    {
      "segundos": 39.953,
      "wal_retido_bytes": 601578040,
      "atraso_bytes": 99919144,
      "wal_status": "reserved"
    },
    {
      "segundos": 40.153,
      "wal_retido_bytes": 606008592,
      "atraso_bytes": 104349696,
      "wal_status": "reserved"
    },
    {
      "segundos": 40.354,
      "wal_retido_bytes": 606008592,
      "atraso_bytes": 104349696,
      "wal_status": "reserved"
    },
    {
      "segundos": 40.555,
      "wal_retido_bytes": 606008592,
      "atraso_bytes": 104349696,
      "wal_status": "reserved"
    },
    {
      "segundos": 40.876,
      "wal_retido_bytes": 614421064,
      "atraso_bytes": 112762168,
      "wal_status": "reserved"
    },
    {
      "segundos": 41.076,
      "wal_retido_bytes": 615183632,
      "atraso_bytes": 113524736,
      "wal_status": "reserved"
    },
    {
      "segundos": 41.277,
      "wal_retido_bytes": 617125136,
      "atraso_bytes": 115466240,
      "wal_status": "reserved"
    },
    {
      "segundos": 41.478,
      "wal_retido_bytes": 623015184,
      "atraso_bytes": 121356288,
      "wal_status": "reserved"
    },
    {
      "segundos": 41.679,
      "wal_retido_bytes": 627766544,
      "atraso_bytes": 126107648,
      "wal_status": "reserved"
    },
    {
      "segundos": 41.879,
      "wal_retido_bytes": 628036880,
      "atraso_bytes": 126377984,
      "wal_status": "reserved"
    },
    {
      "segundos": 42.08,
      "wal_retido_bytes": 631501224,
      "atraso_bytes": 129842328,
      "wal_status": "reserved"
    },
    {
      "segundos": 42.281,
      "wal_retido_bytes": 639046928,
      "atraso_bytes": 137388032,
      "wal_status": "reserved"
    },
    {
      "segundos": 42.482,
      "wal_retido_bytes": 639046928,
      "atraso_bytes": 137388032,
      "wal_status": "reserved"
    },
    {
      "segundos": 42.683,
      "wal_retido_bytes": 639046928,
      "atraso_bytes": 137388032,
      "wal_status": "reserved"
    },
    {
      "segundos": 42.883,
      "wal_retido_bytes": 642077968,
      "atraso_bytes": 140419072,
      "wal_status": "reserved"
    },
    {
      "segundos": 43.084,
      "wal_retido_bytes": 648738064,
      "atraso_bytes": 147079168,
      "wal_status": "reserved"
    },
    {
      "segundos": 43.285,
      "wal_retido_bytes": 649999632,
      "atraso_bytes": 148340736,
      "wal_status": "reserved"
    },
    {
      "segundos": 43.486,
      "wal_retido_bytes": 649999632,
      "atraso_bytes": 148340736,
      "wal_status": "reserved"
    },
    {
      "segundos": 43.686,
      "wal_retido_bytes": 657126672,
      "atraso_bytes": 155467776,
      "wal_status": "reserved"
    },
    {
      "segundos": 43.887,
      "wal_retido_bytes": 660960528,
      "atraso_bytes": 159301632,
      "wal_status": "reserved"
    },
    {
      "segundos": 44.088,
      "wal_retido_bytes": 663477360,
      "atraso_bytes": 161818464,
      "wal_status": "reserved"
    },
    {
      "segundos": 44.289,
      "wal_retido_bytes": 667882768,
      "atraso_bytes": 166223872,
      "wal_status": "reserved"
    },
    {
      "segundos": 44.489,
      "wal_retido_bytes": 667899152,
      "atraso_bytes": 166240256,
      "wal_status": "reserved"
    },
    {
      "segundos": 44.69,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 166259488,
      "wal_status": "reserved"
    },
    {
      "segundos": 44.891,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 166259488,
      "wal_status": "reserved"
    },
    {
      "segundos": 45.092,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 166259488,
      "wal_status": "reserved"
    },
    {
      "segundos": 45.293,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 166259488,
      "wal_status": "reserved"
    },
    {
      "segundos": 45.494,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 166259488,
      "wal_status": "reserved"
    },
    {
      "segundos": 45.695,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 166259488,
      "wal_status": "reserved"
    },
    {
      "segundos": 45.895,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 166259488,
      "wal_status": "reserved"
    },
    {
      "segundos": 46.096,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 166259488,
      "wal_status": "reserved"
    },
    {
      "segundos": 46.297,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 46.498,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 46.699,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 46.899,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 47.1,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 47.301,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 47.502,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 47.702,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 47.903,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 48.104,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 48.305,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 48.506,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 48.707,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 48.908,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 49.108,
      "wal_retido_bytes": 667918384,
      "atraso_bytes": 61909792,
      "wal_status": "reserved"
    },
    {
      "segundos": 49.312,
      "wal_retido_bytes": 667919944,
      "atraso_bytes": 61911352,
      "wal_status": "reserved"
    },
    {
      "segundos": 49.512,
      "wal_retido_bytes": 667919944,
      "atraso_bytes": 61911352,
      "wal_status": "reserved"
    },
    {
      "segundos": 49.713,
      "wal_retido_bytes": 668089392,
      "atraso_bytes": 62080800,
      "wal_status": "reserved"
    },
    {
      "segundos": 49.914,
      "wal_retido_bytes": 678090000,
      "atraso_bytes": 72081408,
      "wal_status": "reserved"
    },
    {
      "segundos": 50.115,
      "wal_retido_bytes": 690681104,
      "atraso_bytes": 84672512,
      "wal_status": "reserved"
    },
    {
      "segundos": 50.316,
      "wal_retido_bytes": 694875408,
      "atraso_bytes": 88866816,
      "wal_status": "reserved"
    },
    {
      "segundos": 50.516,
      "wal_retido_bytes": 695768616,
      "atraso_bytes": 89760024,
      "wal_status": "reserved"
    },
    {
      "segundos": 50.717,
      "wal_retido_bytes": 695776528,
      "atraso_bytes": 89767936,
      "wal_status": "reserved"
    },
    {
      "segundos": 50.99,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 51.191,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 51.392,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 51.593,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 51.794,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 51.994,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 52.195,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 52.396,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 52.597,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 52.798,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 52.999,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 53.199,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 53.4,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 53.601,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 53.801,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 54.002,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 54.203,
      "wal_retido_bytes": 695783864,
      "atraso_bytes": 89775272,
      "wal_status": "reserved"
    },
    {
      "segundos": 54.404,
      "wal_retido_bytes": 697767184,
      "atraso_bytes": 91758592,
      "wal_status": "reserved"
    },
    {
      "segundos": 54.605,
      "wal_retido_bytes": 699716880,
      "atraso_bytes": 93708288,
      "wal_status": "reserved"
    },
    {
      "segundos": 54.806,
      "wal_retido_bytes": 703755536,
      "atraso_bytes": 97746944,
      "wal_status": "reserved"
    },
    {
      "segundos": 55.006,
      "wal_retido_bytes": 707458320,
      "atraso_bytes": 101449728,
      "wal_status": "reserved"
    },
    {
      "segundos": 55.207,
      "wal_retido_bytes": 711652624,
      "atraso_bytes": 105644032,
      "wal_status": "reserved"
    },
    {
      "segundos": 55.408,
      "wal_retido_bytes": 715846928,
      "atraso_bytes": 109838336,
      "wal_status": "reserved"
    },
    {
      "segundos": 55.609,
      "wal_retido_bytes": 720041232,
      "atraso_bytes": 114032640,
      "wal_status": "reserved"
    },
    {
      "segundos": 55.81,
      "wal_retido_bytes": 724235536,
      "atraso_bytes": 118226944,
      "wal_status": "reserved"
    },
    {
      "segundos": 56.011,
      "wal_retido_bytes": 728429840,
      "atraso_bytes": 122421248,
      "wal_status": "reserved"
    },
    {
      "segundos": 56.211,
      "wal_retido_bytes": 732624144,
      "atraso_bytes": 126615552,
      "wal_status": "reserved"
    },
    {
      "segundos": 56.412,
      "wal_retido_bytes": 736818448,
      "atraso_bytes": 130809856,
      "wal_status": "reserved"
    },
    {
      "segundos": 56.613,
      "wal_retido_bytes": 740160784,
      "atraso_bytes": 134152192,
      "wal_status": "reserved"
    },
    {
      "segundos": 56.814,
      "wal_retido_bytes": 741012752,
      "atraso_bytes": 135004160,
      "wal_status": "reserved"
    },
    {
      "segundos": 57.015,
      "wal_retido_bytes": 745207056,
      "atraso_bytes": 139198464,
      "wal_status": "reserved"
    },
    {
      "segundos": 57.216,
      "wal_retido_bytes": 749401360,
      "atraso_bytes": 143392768,
      "wal_status": "reserved"
    },
    {
      "segundos": 57.417,
      "wal_retido_bytes": 752170256,
      "atraso_bytes": 146161664,
      "wal_status": "reserved"
    },
    {
      "segundos": 57.618,
      "wal_retido_bytes": 754201872,
      "atraso_bytes": 148193280,
      "wal_status": "reserved"
    },
    {
      "segundos": 57.819,
      "wal_retido_bytes": 757789968,
      "atraso_bytes": 151781376,
      "wal_status": "reserved"
    },
    {
      "segundos": 58.02,
      "wal_retido_bytes": 761984272,
      "atraso_bytes": 155975680,
      "wal_status": "reserved"
    },
    {
      "segundos": 58.221,
      "wal_retido_bytes": 766178576,
      "atraso_bytes": 160169984,
      "wal_status": "reserved"
    },
    {
      "segundos": 58.422,
      "wal_retido_bytes": 770372880,
      "atraso_bytes": 164364288,
      "wal_status": "reserved"
    },
    {
      "segundos": 58.622,
      "wal_retido_bytes": 774272272,
      "atraso_bytes": 168263680,
      "wal_status": "reserved"
    },
    {
      "segundos": 58.823,
      "wal_retido_bytes": 775533840,
      "atraso_bytes": 169525248,
      "wal_status": "reserved"
    },
    {
      "segundos": 59.024,
      "wal_retido_bytes": 778982672,
      "atraso_bytes": 172974080,
      "wal_status": "reserved"
    },
    {
      "segundos": 59.225,
      "wal_retido_bytes": 782955792,
      "atraso_bytes": 176947200,
      "wal_status": "reserved"
    },
    {
      "segundos": 59.426,
      "wal_retido_bytes": 787150096,
      "atraso_bytes": 181141504,
      "wal_status": "reserved"
    },
    {
      "segundos": 59.626,
      "wal_retido_bytes": 791344400,
      "atraso_bytes": 185335808,
      "wal_status": "reserved"
    },
    {
      "segundos": 59.827,
      "wal_retido_bytes": 795538704,
      "atraso_bytes": 189530112,
      "wal_status": "reserved"
    },
    {
      "segundos": 60.028,
      "wal_retido_bytes": 799552784,
      "atraso_bytes": 193544192,
      "wal_status": "reserved"
    },
    {
      "segundos": 60.229,
      "wal_retido_bytes": 801158416,
      "atraso_bytes": 195149824,
      "wal_status": "reserved"
    },
    {
      "segundos": 60.429,
      "wal_retido_bytes": 804918544,
      "atraso_bytes": 198909952,
      "wal_status": "reserved"
    },
    {
      "segundos": 60.63,
      "wal_retido_bytes": 808482064,
      "atraso_bytes": 202473472,
      "wal_status": "reserved"
    },
    {
      "segundos": 60.831,
      "wal_retido_bytes": 812315920,
      "atraso_bytes": 206307328,
      "wal_status": "reserved"
    },
    {
      "segundos": 61.2,
      "wal_retido_bytes": 820704528,
      "atraso_bytes": 152786144,
      "wal_status": "reserved"
    },
    {
      "segundos": 61.401,
      "wal_retido_bytes": 823506192,
      "atraso_bytes": 155587808,
      "wal_status": "reserved"
    },
    {
      "segundos": 61.602,
      "wal_retido_bytes": 826955024,
      "atraso_bytes": 159036640,
      "wal_status": "reserved"
    },
    {
      "segundos": 61.803,
      "wal_retido_bytes": 830756112,
      "atraso_bytes": 162837728,
      "wal_status": "reserved"
    },
    {
      "segundos": 62.003,
      "wal_retido_bytes": 834721040,
      "atraso_bytes": 166802656,
      "wal_status": "reserved"
    },
    {
      "segundos": 62.204,
      "wal_retido_bytes": 838579472,
      "atraso_bytes": 170661088,
      "wal_status": "reserved"
    },
    {
      "segundos": 62.405,
      "wal_retido_bytes": 842265872,
      "atraso_bytes": 174347488,
      "wal_status": "reserved"
    },
    {
      "segundos": 62.605,
      "wal_retido_bytes": 846296336,
      "atraso_bytes": 178377952,
      "wal_status": "reserved"
    },
    {
      "segundos": 62.806,
      "wal_retido_bytes": 850261264,
      "atraso_bytes": 182342880,
      "wal_status": "reserved"
    },
    {
      "segundos": 63.007,
      "wal_retido_bytes": 854258960,
      "atraso_bytes": 186340576,
      "wal_status": "reserved"
    },
    {
      "segundos": 63.208,
      "wal_retido_bytes": 858453264,
      "atraso_bytes": 190534880,
      "wal_status": "reserved"
    },
    {
      "segundos": 63.408,
      "wal_retido_bytes": 862622992,
      "atraso_bytes": 194704608,
      "wal_status": "reserved"
    },
    {
      "segundos": 63.609,
      "wal_retido_bytes": 865121552,
      "atraso_bytes": 197203168,
      "wal_status": "reserved"
    },
    {
      "segundos": 63.81,
      "wal_retido_bytes": 868865296,
      "atraso_bytes": 200946912,
      "wal_status": "reserved"
    },
    {
      "segundos": 64.011,
      "wal_retido_bytes": 872658192,
      "atraso_bytes": 204739808,
      "wal_status": "reserved"
    },
    {
      "segundos": 64.211,
      "wal_retido_bytes": 876082448,
      "atraso_bytes": 208164064,
      "wal_status": "reserved"
    },
    {
      "segundos": 64.412,
      "wal_retido_bytes": 879916304,
      "atraso_bytes": 211997920,
      "wal_status": "reserved"
    },
    {
      "segundos": 64.613,
      "wal_retido_bytes": 883717392,
      "atraso_bytes": 215799008,
      "wal_status": "reserved"
    },
    {
      "segundos": 64.814,
      "wal_retido_bytes": 887813392,
      "atraso_bytes": 219895008,
      "wal_status": "reserved"
    },
    {
      "segundos": 65.014,
      "wal_retido_bytes": 892007696,
      "atraso_bytes": 224089312,
      "wal_status": "reserved"
    },
    {
      "segundos": 65.215,
      "wal_retido_bytes": 896202000,
      "atraso_bytes": 228283616,
      "wal_status": "reserved"
    },
    {
      "segundos": 65.416,
      "wal_retido_bytes": 900396304,
      "atraso_bytes": 232477920,
      "wal_status": "reserved"
    },
    {
      "segundos": 65.616,
      "wal_retido_bytes": 904590608,
      "atraso_bytes": 236672224,
      "wal_status": "reserved"
    },
    {
      "segundos": 65.818,
      "wal_retido_bytes": 908449040,
      "atraso_bytes": 240530656,
      "wal_status": "reserved"
    },
    {
      "segundos": 66.018,
      "wal_retido_bytes": 909784336,
      "atraso_bytes": 241865952,
      "wal_status": "reserved"
    },
    {
      "segundos": 66.219,
      "wal_retido_bytes": 913511696,
      "atraso_bytes": 245593312,
      "wal_status": "reserved"
    },
    {
      "segundos": 66.42,
      "wal_retido_bytes": 917329168,
      "atraso_bytes": 249410784,
      "wal_status": "reserved"
    },
    {
      "segundos": 66.621,
      "wal_retido_bytes": 921367824,
      "atraso_bytes": 253449440,
      "wal_status": "reserved"
    },
    {
      "segundos": 66.822,
      "wal_retido_bytes": 925562128,
      "atraso_bytes": 257643744,
      "wal_status": "reserved"
    },
    {
      "segundos": 67.022,
      "wal_retido_bytes": 929756432,
      "atraso_bytes": 261838048,
      "wal_status": "reserved"
    },
    {
      "segundos": 67.223,
      "wal_retido_bytes": 933950736,
      "atraso_bytes": 266032352,
      "wal_status": "reserved"
    },
    {
      "segundos": 67.424,
      "wal_retido_bytes": 938145040,
      "atraso_bytes": 270226656,
      "wal_status": "reserved"
    },
    {
      "segundos": 67.625,
      "wal_retido_bytes": 938145040,
      "atraso_bytes": 270226656,
      "wal_status": "reserved"
    },
    {
      "segundos": 67.826,
      "wal_retido_bytes": 942085392,
      "atraso_bytes": 274167008,
      "wal_status": "reserved"
    },
    {
      "segundos": 68.026,
      "wal_retido_bytes": 946525456,
      "atraso_bytes": 278607072,
      "wal_status": "reserved"
    },
    {
      "segundos": 68.227,
      "wal_retido_bytes": 950727952,
      "atraso_bytes": 282809568,
      "wal_status": "reserved"
    },
    {
      "segundos": 68.428,
      "wal_retido_bytes": 954922256,
      "atraso_bytes": 287003872,
      "wal_status": "reserved"
    },
    {
      "segundos": 68.629,
      "wal_retido_bytes": 959116560,
      "atraso_bytes": 291198176,
      "wal_status": "reserved"
    },
    {
      "segundos": 68.83,
      "wal_retido_bytes": 963302672,
      "atraso_bytes": 295384288,
      "wal_status": "reserved"
    },
    {
      "segundos": 69.03,
      "wal_retido_bytes": 967505168,
      "atraso_bytes": 299586784,
      "wal_status": "reserved"
    },
    {
      "segundos": 69.231,
      "wal_retido_bytes": 971699472,
      "atraso_bytes": 303781088,
      "wal_status": "reserved"
    },
    {
      "segundos": 69.432,
      "wal_retido_bytes": 975729936,
      "atraso_bytes": 307811552,
      "wal_status": "reserved"
    },
    {
      "segundos": 69.633,
      "wal_retido_bytes": 980071696,
      "atraso_bytes": 312153312,
      "wal_status": "reserved"
    },
    {
      "segundos": 69.833,
      "wal_retido_bytes": 984282384,
      "atraso_bytes": 316364000,
      "wal_status": "reserved"
    },
    {
      "segundos": 70.034,
      "wal_retido_bytes": 988476688,
      "atraso_bytes": 320558304,
      "wal_status": "reserved"
    },
    {
      "segundos": 70.235,
      "wal_retido_bytes": 992670992,
      "atraso_bytes": 324752608,
      "wal_status": "reserved"
    },
    {
      "segundos": 70.436,
      "wal_retido_bytes": 996373776,
      "atraso_bytes": 328455392,
      "wal_status": "reserved"
    },
    {
      "segundos": 70.637,
      "wal_retido_bytes": 996988176,
      "atraso_bytes": 329069792,
      "wal_status": "reserved"
    },
    {
      "segundos": 70.837,
      "wal_retido_bytes": 1001059600,
      "atraso_bytes": 333141216,
      "wal_status": "reserved"
    },
    {
      "segundos": 71.038,
      "wal_retido_bytes": 1005253904,
      "atraso_bytes": 337335520,
      "wal_status": "reserved"
    },
    {
      "segundos": 71.341,
      "wal_retido_bytes": 1010948144,
      "atraso_bytes": 343029760,
      "wal_status": "reserved"
    },
    {
      "segundos": 71.541,
      "wal_retido_bytes": 1015026960,
      "atraso_bytes": 347108576,
      "wal_status": "reserved"
    },
    {
      "segundos": 71.742,
      "wal_retido_bytes": 391246616,
      "atraso_bytes": 165027840,
      "wal_status": "reserved"
    },
    {
      "segundos": 71.943,
      "wal_retido_bytes": 395539224,
      "atraso_bytes": 169320448,
      "wal_status": "reserved"
    },
    {
      "segundos": 72.144,
      "wal_retido_bytes": 401718312,
      "atraso_bytes": 175499536,
      "wal_status": "reserved"
    },
    {
      "segundos": 72.345,
      "wal_retido_bytes": 404222744,
      "atraso_bytes": 178003968,
      "wal_status": "reserved"
    },
    {
      "segundos": 72.546,
      "wal_retido_bytes": 410768152,
      "atraso_bytes": 184549376,
      "wal_status": "reserved"
    },
    {
      "segundos": 72.748,
      "wal_retido_bytes": 414838800,
      "atraso_bytes": 188620024,
      "wal_status": "reserved"
    },
    {
      "segundos": 72.949,
      "wal_retido_bytes": 414962456,
      "atraso_bytes": 188743680,
      "wal_status": "reserved"
    },
    {
      "segundos": 73.15,
      "wal_retido_bytes": 415437592,
      "atraso_bytes": 189218816,
      "wal_status": "reserved"
    },
    {
      "segundos": 73.351,
      "wal_retido_bytes": 415494936,
      "atraso_bytes": 189276160,
      "wal_status": "reserved"
    },
    {
      "segundos": 73.552,
      "wal_retido_bytes": 415544088,
      "atraso_bytes": 189325312,
      "wal_status": "reserved"
    },
    {
      "segundos": 73.753,
      "wal_retido_bytes": 430166808,
      "atraso_bytes": 203948032,
      "wal_status": "reserved"
    },
    {
      "segundos": 73.953,
      "wal_retido_bytes": 435933976,
      "atraso_bytes": 209715200,
      "wal_status": "reserved"
    },
    {
      "segundos": 74.154,
      "wal_retido_bytes": 440146472,
      "atraso_bytes": 213927696,
      "wal_status": "reserved"
    },
    {
      "segundos": 74.355,
      "wal_retido_bytes": 440242968,
      "atraso_bytes": 214024192,
      "wal_status": "reserved"
    },
    {
      "segundos": 74.556,
      "wal_retido_bytes": 440292120,
      "atraso_bytes": 214073344,
      "wal_status": "reserved"
    },
    {
      "segundos": 74.757,
      "wal_retido_bytes": 440365848,
      "atraso_bytes": 214147072,
      "wal_status": "reserved"
    },
    {
      "segundos": 74.957,
      "wal_retido_bytes": 443691800,
      "atraso_bytes": 217473024,
      "wal_status": "reserved"
    },
    {
      "segundos": 75.158,
      "wal_retido_bytes": 444322584,
      "atraso_bytes": 218103808,
      "wal_status": "reserved"
    },
    {
      "segundos": 75.359,
      "wal_retido_bytes": 448516888,
      "atraso_bytes": 222298112,
      "wal_status": "reserved"
    },
    {
      "segundos": 75.56,
      "wal_retido_bytes": 452711192,
      "atraso_bytes": 226492416,
      "wal_status": "reserved"
    },
    {
      "segundos": 75.76,
      "wal_retido_bytes": 456905496,
      "atraso_bytes": 230686720,
      "wal_status": "reserved"
    },
    {
      "segundos": 75.961,
      "wal_retido_bytes": 461099800,
      "atraso_bytes": 234881024,
      "wal_status": "reserved"
    },
    {
      "segundos": 76.162,
      "wal_retido_bytes": 465294104,
      "atraso_bytes": 239075328,
      "wal_status": "reserved"
    },
    {
      "segundos": 76.363,
      "wal_retido_bytes": 469488408,
      "atraso_bytes": 243269632,
      "wal_status": "reserved"
    },
    {
      "segundos": 76.564,
      "wal_retido_bytes": 473682712,
      "atraso_bytes": 247463936,
      "wal_status": "reserved"
    },
    {
      "segundos": 76.764,
      "wal_retido_bytes": 477877016,
      "atraso_bytes": 251658240,
      "wal_status": "reserved"
    },
    {
      "segundos": 76.965,
      "wal_retido_bytes": 482071320,
      "atraso_bytes": 255852544,
      "wal_status": "reserved"
    },
    {
      "segundos": 77.166,
      "wal_retido_bytes": 486191896,
      "atraso_bytes": 259973120,
      "wal_status": "reserved"
    },
    {
      "segundos": 77.367,
      "wal_retido_bytes": 486191896,
      "atraso_bytes": 259973120,
      "wal_status": "reserved"
    },
    {
      "segundos": 77.567,
      "wal_retido_bytes": 490294416,
      "atraso_bytes": 264075640,
      "wal_status": "reserved"
    },
    {
      "segundos": 77.768,
      "wal_retido_bytes": 496799936,
      "atraso_bytes": 270581160,
      "wal_status": "reserved"
    },
    {
      "segundos": 77.969,
      "wal_retido_bytes": 501117344,
      "atraso_bytes": 274898568,
      "wal_status": "reserved"
    },
    {
      "segundos": 78.17,
      "wal_retido_bytes": 502740480,
      "atraso_bytes": 276521704,
      "wal_status": "reserved"
    },
    {
      "segundos": 78.37,
      "wal_retido_bytes": 506511136,
      "atraso_bytes": 280292360,
      "wal_status": "reserved"
    },
    {
      "segundos": 78.571,
      "wal_retido_bytes": 507917080,
      "atraso_bytes": 281698304,
      "wal_status": "reserved"
    },
    {
      "segundos": 78.772,
      "wal_retido_bytes": 511431448,
      "atraso_bytes": 285212672,
      "wal_status": "reserved"
    },
    {
      "segundos": 78.973,
      "wal_retido_bytes": 518353560,
      "atraso_bytes": 292134784,
      "wal_status": "reserved"
    },
    {
      "segundos": 79.173,
      "wal_retido_bytes": 521588224,
      "atraso_bytes": 295369448,
      "wal_status": "reserved"
    },
    {
      "segundos": 79.374,
      "wal_retido_bytes": 523309848,
      "atraso_bytes": 297091072,
      "wal_status": "reserved"
    },
    {
      "segundos": 79.575,
      "wal_retido_bytes": 530193328,
      "atraso_bytes": 303974552,
      "wal_status": "reserved"
    },
    {
      "segundos": 79.776,
      "wal_retido_bytes": 531632920,
      "atraso_bytes": 116088832,
      "wal_status": "reserved"
    },
    {
      "segundos": 79.977,
      "wal_retido_bytes": 537195224,
      "atraso_bytes": 121651136,
      "wal_status": "reserved"
    },
    {
      "segundos": 80.177,
      "wal_retido_bytes": 539824920,
      "atraso_bytes": 124280832,
      "wal_status": "reserved"
    },
    {
      "segundos": 80.378,
      "wal_retido_bytes": 546864384,
      "atraso_bytes": 131320296,
      "wal_status": "reserved"
    },
    {
      "segundos": 80.579,
      "wal_retido_bytes": 548320024,
      "atraso_bytes": 132775936,
      "wal_status": "reserved"
    },
    {
      "segundos": 80.78,
      "wal_retido_bytes": 554925728,
      "atraso_bytes": 139381640,
      "wal_status": "reserved"
    },
    {
      "segundos": 80.98,
      "wal_retido_bytes": 556487448,
      "atraso_bytes": 140943360,
      "wal_status": "reserved"
    },
    {
      "segundos": 81.181,
      "wal_retido_bytes": 563529272,
      "atraso_bytes": 147985184,
      "wal_status": "reserved"
    },
    {
      "segundos": 81.713,
      "wal_retido_bytes": 575460120,
      "atraso_bytes": 159907840,
      "wal_status": "reserved"
    },
    {
      "segundos": 81.914,
      "wal_retido_bytes": 582734616,
      "atraso_bytes": 167190528,
      "wal_status": "reserved"
    },
    {
      "segundos": 82.114,
      "wal_retido_bytes": 583529240,
      "atraso_bytes": 167985152,
      "wal_status": "reserved"
    },
    {
      "segundos": 82.315,
      "wal_retido_bytes": 583570200,
      "atraso_bytes": 168026112,
      "wal_status": "reserved"
    },
    {
      "segundos": 82.516,
      "wal_retido_bytes": 583652120,
      "atraso_bytes": 168108032,
      "wal_status": "reserved"
    },
    {
      "segundos": 82.717,
      "wal_retido_bytes": 583701272,
      "atraso_bytes": 168157184,
      "wal_status": "reserved"
    },
    {
      "segundos": 82.917,
      "wal_retido_bytes": 583750424,
      "atraso_bytes": 168206336,
      "wal_status": "reserved"
    },
    {
      "segundos": 83.118,
      "wal_retido_bytes": 583840536,
      "atraso_bytes": 168296448,
      "wal_status": "reserved"
    },
    {
      "segundos": 83.319,
      "wal_retido_bytes": 583889688,
      "atraso_bytes": 168345600,
      "wal_status": "reserved"
    },
    {
      "segundos": 83.52,
      "wal_retido_bytes": 583930648,
      "atraso_bytes": 168386560,
      "wal_status": "reserved"
    },
    {
      "segundos": 83.721,
      "wal_retido_bytes": 583930648,
      "atraso_bytes": 168386560,
      "wal_status": "reserved"
    },
    {
      "segundos": 83.922,
      "wal_retido_bytes": 583937520,
      "atraso_bytes": 168393432,
      "wal_status": "reserved"
    },
    {
      "segundos": 84.122,
      "wal_retido_bytes": 586928920,
      "atraso_bytes": 171384832,
      "wal_status": "reserved"
    },
    {
      "segundos": 84.323,
      "wal_retido_bytes": 591123224,
      "atraso_bytes": 175579136,
      "wal_status": "reserved"
    },
    {
      "segundos": 84.524,
      "wal_retido_bytes": 597898008,
      "atraso_bytes": 182353920,
      "wal_status": "reserved"
    },
    {
      "segundos": 84.725,
      "wal_retido_bytes": 600191768,
      "atraso_bytes": 184647680,
      "wal_status": "reserved"
    },
    {
      "segundos": 84.925,
      "wal_retido_bytes": 607900440,
      "atraso_bytes": 192356352,
      "wal_status": "reserved"
    },
    {
      "segundos": 85.126,
      "wal_retido_bytes": 609448728,
      "atraso_bytes": 193904640,
      "wal_status": "reserved"
    },
    {
      "segundos": 85.327,
      "wal_retido_bytes": 613841800,
      "atraso_bytes": 198297712,
      "wal_status": "reserved"
    },
    {
      "segundos": 85.528,
      "wal_retido_bytes": 613856392,
      "atraso_bytes": 198312304,
      "wal_status": "reserved"
    },
    {
      "segundos": 85.729,
      "wal_retido_bytes": 629650200,
      "atraso_bytes": 214097920,
      "wal_status": "reserved"
    },
    {
      "segundos": 85.929,
      "wal_retido_bytes": 648123160,
      "atraso_bytes": 232570880,
      "wal_status": "reserved"
    },
    {
      "segundos": 86.13,
      "wal_retido_bytes": 665998104,
      "atraso_bytes": 250454016,
      "wal_status": "reserved"
    },
    {
      "segundos": 86.331,
      "wal_retido_bytes": 683946776,
      "atraso_bytes": 268402688,
      "wal_status": "reserved"
    },
    {
      "segundos": 86.532,
      "wal_retido_bytes": 700674840,
      "atraso_bytes": 285130752,
      "wal_status": "reserved"
    },
    {
      "segundos": 86.733,
      "wal_retido_bytes": 718549784,
      "atraso_bytes": 303005696,
      "wal_status": "reserved"
    },
    {
      "segundos": 86.934,
      "wal_retido_bytes": 734171928,
      "atraso_bytes": 318627840,
      "wal_status": "reserved"
    },
    {
      "segundos": 87.134,
      "wal_retido_bytes": 752284440,
      "atraso_bytes": 336740352,
      "wal_status": "reserved"
    },
    {
      "segundos": 87.335,
      "wal_retido_bytes": 769127192,
      "atraso_bytes": 353583104,
      "wal_status": "reserved"
    },
    {
      "segundos": 87.536,
      "wal_retido_bytes": 783938328,
      "atraso_bytes": 368386048,
      "wal_status": "reserved"
    },
    {
      "segundos": 87.737,
      "wal_retido_bytes": 799994648,
      "atraso_bytes": 384450560,
      "wal_status": "reserved"
    },
    {
      "segundos": 87.937,
      "wal_retido_bytes": 814306072,
      "atraso_bytes": 398761984,
      "wal_status": "reserved"
    },
    {
      "segundos": 88.138,
      "wal_retido_bytes": 828117784,
      "atraso_bytes": 412573696,
      "wal_status": "reserved"
    },
    {
      "segundos": 88.339,
      "wal_retido_bytes": 843445016,
      "atraso_bytes": 427900928,
      "wal_status": "reserved"
    },
    {
      "segundos": 88.54,
      "wal_retido_bytes": 864416536,
      "atraso_bytes": 448872448,
      "wal_status": "reserved"
    },
    {
      "segundos": 88.74,
      "wal_retido_bytes": 886919960,
      "atraso_bytes": 471375872,
      "wal_status": "reserved"
    },
    {
      "segundos": 88.941,
      "wal_retido_bytes": 908735256,
      "atraso_bytes": 493191168,
      "wal_status": "reserved"
    },
    {
      "segundos": 89.142,
      "wal_retido_bytes": 930747160,
      "atraso_bytes": 515203072,
      "wal_status": "reserved"
    },
    {
      "segundos": 89.343,
      "wal_retido_bytes": 953373464,
      "atraso_bytes": 537829376,
      "wal_status": "reserved"
    },
    {
      "segundos": 89.544,
      "wal_retido_bytes": 975999768,
      "atraso_bytes": 560455680,
      "wal_status": "reserved"
    },
    {
      "segundos": 89.745,
      "wal_retido_bytes": 999494424,
      "atraso_bytes": 583942144,
      "wal_status": "reserved"
    },
    {
      "segundos": 89.945,
      "wal_retido_bytes": 1022513944,
      "atraso_bytes": 606961664,
      "wal_status": "reserved"
    },
    {
      "segundos": 90.146,
      "wal_retido_bytes": 1045517080,
      "atraso_bytes": 629972992,
      "wal_status": "reserved"
    },
    {
      "segundos": 90.347,
      "wal_retido_bytes": 1068995352,
      "atraso_bytes": 653451264,
      "wal_status": "reserved"
    },
    {
      "segundos": 90.548,
      "wal_retido_bytes": 1077662488,
      "atraso_bytes": 662118400,
      "wal_status": "reserved"
    },
    {
      "segundos": 90.749,
      "wal_retido_bytes": 1080283928,
      "atraso_bytes": 664739840,
      "wal_status": "reserved"
    },
    {
      "segundos": 90.95,
      "wal_retido_bytes": 1080283928,
      "atraso_bytes": 664739840,
      "wal_status": "reserved"
    },
    {
      "segundos": 91.151,
      "wal_retido_bytes": 1080283928,
      "atraso_bytes": 664739840,
      "wal_status": "reserved"
    },
    {
      "segundos": 91.351,
      "wal_retido_bytes": 1080283928,
      "atraso_bytes": 664739840,
      "wal_status": "reserved"
    },
    {
      "segundos": 91.553,
      "wal_retido_bytes": 1080283928,
      "atraso_bytes": 664739840,
      "wal_status": "reserved"
    },
    {
      "segundos": 92.015,
      "wal_retido_bytes": 1102820120,
      "atraso_bytes": 687276032,
      "wal_status": "reserved"
    },
    {
      "segundos": 92.216,
      "wal_retido_bytes": 1153717016,
      "atraso_bytes": 738172928,
      "wal_status": "extended"
    },
    {
      "segundos": 92.417,
      "wal_retido_bytes": 1192719128,
      "atraso_bytes": 777175040,
      "wal_status": "extended"
    },
    {
      "segundos": 92.618,
      "wal_retido_bytes": 1195103000,
      "atraso_bytes": 779558912,
      "wal_status": "extended"
    },
    {
      "segundos": 92.818,
      "wal_retido_bytes": 1196987160,
      "atraso_bytes": 781443072,
      "wal_status": "extended"
    },
    {
      "segundos": 93.019,
      "wal_retido_bytes": 1196987160,
      "atraso_bytes": 781443072,
      "wal_status": "extended"
    },
    {
      "segundos": 93.22,
      "wal_retido_bytes": 1196987160,
      "atraso_bytes": 781443072,
      "wal_status": "extended"
    },
    {
      "segundos": 93.421,
      "wal_retido_bytes": 1196987160,
      "atraso_bytes": 781443072,
      "wal_status": "extended"
    },
    {
      "segundos": 93.621,
      "wal_retido_bytes": 796094768,
      "atraso_bytes": 629172560,
      "wal_status": "reserved"
    },
    {
      "segundos": 93.822,
      "wal_retido_bytes": 796094768,
      "atraso_bytes": 629172560,
      "wal_status": "reserved"
    },
    {
      "segundos": 94.023,
      "wal_retido_bytes": 796094768,
      "atraso_bytes": 629172560,
      "wal_status": "reserved"
    },
    {
      "segundos": 94.223,
      "wal_retido_bytes": 796094768,
      "atraso_bytes": 629172560,
      "wal_status": "reserved"
    },
    {
      "segundos": 94.424,
      "wal_retido_bytes": 796094768,
      "atraso_bytes": 629172560,
      "wal_status": "reserved"
    },
    {
      "segundos": 94.625,
      "wal_retido_bytes": 796094768,
      "atraso_bytes": 629172560,
      "wal_status": "reserved"
    },
    {
      "segundos": 94.826,
      "wal_retido_bytes": 796094768,
      "atraso_bytes": 629172560,
      "wal_status": "reserved"
    },
    {
      "segundos": 95.026,
      "wal_retido_bytes": 796094768,
      "atraso_bytes": 629172560,
      "wal_status": "reserved"
    },
    {
      "segundos": 95.227,
      "wal_retido_bytes": 796094768,
      "atraso_bytes": 629172560,
      "wal_status": "reserved"
    },
    {
      "segundos": 95.428,
      "wal_retido_bytes": 796094768,
      "atraso_bytes": 629172560,
      "wal_status": "reserved"
    },
    {
      "segundos": 95.629,
      "wal_retido_bytes": 796111152,
      "atraso_bytes": 629188944,
      "wal_status": "reserved"
    },
    {
      "segundos": 95.83,
      "wal_retido_bytes": 796111152,
      "atraso_bytes": 629188944,
      "wal_status": "reserved"
    },
    {
      "segundos": 96.031,
      "wal_retido_bytes": 796114856,
      "atraso_bytes": 629192648,
      "wal_status": "reserved"
    },
    {
      "segundos": 96.232,
      "wal_retido_bytes": 796114856,
      "atraso_bytes": 629192648,
      "wal_status": "reserved"
    },
    {
      "segundos": 96.432,
      "wal_retido_bytes": 796127536,
      "atraso_bytes": 629205328,
      "wal_status": "reserved"
    },
    {
      "segundos": 96.633,
      "wal_retido_bytes": 799093040,
      "atraso_bytes": 632170832,
      "wal_status": "reserved"
    },
    {
      "segundos": 96.834,
      "wal_retido_bytes": 804999472,
      "atraso_bytes": 638077264,
      "wal_status": "reserved"
    },
    {
      "segundos": 97.034,
      "wal_retido_bytes": 811397424,
      "atraso_bytes": 644475216,
      "wal_status": "reserved"
    },
    {
      "segundos": 97.235,
      "wal_retido_bytes": 815182128,
      "atraso_bytes": 648259920,
      "wal_status": "reserved"
    },
    {
      "segundos": 97.437,
      "wal_retido_bytes": 816181552,
      "atraso_bytes": 649259344,
      "wal_status": "reserved"
    },
    {
      "segundos": 97.637,
      "wal_retido_bytes": 816181552,
      "atraso_bytes": 649259344,
      "wal_status": "reserved"
    },
    {
      "segundos": 97.838,
      "wal_retido_bytes": 816181552,
      "atraso_bytes": 649259344,
      "wal_status": "reserved"
    },
    {
      "segundos": 98.039,
      "wal_retido_bytes": 816181552,
      "atraso_bytes": 649259344,
      "wal_status": "reserved"
    },
    {
      "segundos": 98.24,
      "wal_retido_bytes": 819376432,
      "atraso_bytes": 652454224,
      "wal_status": "reserved"
    },
    {
      "segundos": 98.44,
      "wal_retido_bytes": 828141872,
      "atraso_bytes": 661219664,
      "wal_status": "reserved"
    },
    {
      "segundos": 98.641,
      "wal_retido_bytes": 833532208,
      "atraso_bytes": 666610000,
      "wal_status": "reserved"
    },
    {
      "segundos": 98.842,
      "wal_retido_bytes": 836153648,
      "atraso_bytes": 669231440,
      "wal_status": "reserved"
    },
    {
      "segundos": 99.043,
      "wal_retido_bytes": 839151920,
      "atraso_bytes": 672229712,
      "wal_status": "reserved"
    },
    {
      "segundos": 99.244,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 99.445,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 99.646,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 99.846,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 100.047,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 100.249,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 100.45,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 100.651,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 100.852,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 101.053,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 101.254,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 101.454,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 101.655,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 101.856,
      "wal_retido_bytes": 839184008,
      "atraso_bytes": 672261800,
      "wal_status": "reserved"
    },
    {
      "segundos": 102.244,
      "wal_retido_bytes": 839184184,
      "atraso_bytes": 672261976,
      "wal_status": "reserved"
    },
    {
      "segundos": 102.445,
      "wal_retido_bytes": 839184184,
      "atraso_bytes": 672261976,
      "wal_status": "reserved"
    },
    {
      "segundos": 102.646,
      "wal_retido_bytes": 839184184,
      "atraso_bytes": 672261976,
      "wal_status": "reserved"
    },
    {
      "segundos": 102.846,
      "wal_retido_bytes": 672262152,
      "atraso_bytes": 43081224,
      "wal_status": "reserved"
    },
    {
      "segundos": 103.047,
      "wal_retido_bytes": 672262152,
      "atraso_bytes": 43081224,
      "wal_status": "reserved"
    },
    {
      "segundos": 103.248,
      "wal_retido_bytes": 672262152,
      "atraso_bytes": 43081224,
      "wal_status": "reserved"
    },
    {
      "segundos": 103.449,
      "wal_retido_bytes": 672262152,
      "atraso_bytes": 43081224,
      "wal_status": "reserved"
    },
    {
      "segundos": 103.65,
      "wal_retido_bytes": 672262152,
      "atraso_bytes": 43081224,
      "wal_status": "reserved"
    },
    {
      "segundos": 103.851,
      "wal_retido_bytes": 672262656,
      "atraso_bytes": 43081728,
      "wal_status": "reserved"
    },
    {
      "segundos": 104.051,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 104.252,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 104.453,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 104.654,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 104.855,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 105.056,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 105.256,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 105.457,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 105.658,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 105.859,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 106.059,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 106.26,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 106.461,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 106.662,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 106.862,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 107.063,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 107.264,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 107.465,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 107.666,
      "wal_retido_bytes": 672269776,
      "atraso_bytes": 43088848,
      "wal_status": "reserved"
    },
    {
      "segundos": 107.866,
      "wal_retido_bytes": 672270848,
      "atraso_bytes": 43089920,
      "wal_status": "reserved"
    },
    {
      "segundos": 108.067,
      "wal_retido_bytes": 672279040,
      "atraso_bytes": 43098112,
      "wal_status": "reserved"
    },
    {
      "segundos": 108.268,
      "wal_retido_bytes": 672279040,
      "atraso_bytes": 43098112,
      "wal_status": "reserved"
    },
    {
      "segundos": 108.47,
      "wal_retido_bytes": 672279040,
      "atraso_bytes": 43098112,
      "wal_status": "reserved"
    },
    {
      "segundos": 108.67,
      "wal_retido_bytes": 672279040,
      "atraso_bytes": 43098112,
      "wal_status": "reserved"
    },
    {
      "segundos": 108.871,
      "wal_retido_bytes": 672279040,
      "atraso_bytes": 43098112,
      "wal_status": "reserved"
    },
    {
      "segundos": 109.072,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 109.272,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 109.473,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 109.674,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 109.874,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 110.075,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 110.276,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 110.477,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 110.678,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 110.878,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 111.079,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 111.28,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 111.481,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 111.681,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 111.882,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 112.083,
      "wal_retido_bytes": 672279552,
      "atraso_bytes": 43098624,
      "wal_status": "reserved"
    },
    {
      "segundos": 112.288,
      "wal_retido_bytes": 672279728,
      "atraso_bytes": 43098800,
      "wal_status": "reserved"
    },
    {
      "segundos": 112.489,
      "wal_retido_bytes": 672279728,
      "atraso_bytes": 43098800,
      "wal_status": "reserved"
    },
    {
      "segundos": 112.69,
      "wal_retido_bytes": 672279728,
      "atraso_bytes": 43098800,
      "wal_status": "reserved"
    },
    {
      "segundos": 112.89,
      "wal_retido_bytes": 672279728,
      "atraso_bytes": 43098800,
      "wal_status": "reserved"
    }
  ]
}
```

</details>


## Validação Compose com volumes novos

Executado o snapshot integrado com projeto `eduinfra-issue60-integrado`, porta
55463, banco e volumes inicialmente vazios:

```bash
docker compose run --build --rm transformacao
```

A carga baixou e ingeriu o Censo nacional; o Compose aguardou o CDC saudável
antes de iniciar o dbt. O build terminou com código 0: 11 modelos e 20 testes
(PASS=31, WARN=0, ERROR=0, SKIP=0), mais catálogo gerado. A construção dbt levou
53,57 s, fora o preparo das fontes e a carga da E2.

Depois do job e de CHECKPOINT explícito: `eduinfra_cdc | reserved | 408 bytes`.
O serviço CDC estava `healthy`; a Gold tinha 181.065 escolas e 3.078.105 itens.
Essa verificação inclui o efeito que faltava no primeiro teste da #48: além do
sucesso da Gold, o fluxo CDC continuou utilizável.

Na verificação final do snapshot #48 + #60, **212 testes passaram** em 81,38 s,
com os mesmos 11 avisos de depreciação existentes e o arquivo opcional de
Hypothesis explicitamente excluído. Essa contagem inclui os testes da #48 e
os seis testes novos da #60.

## Verificação após sincronizar com a main em 7529826

A PR #62 entrou na main durante a preparação desta PR. A branch foi atualizada
por fast-forward e o patch reaplicado sem conflito. Na nova base, a suíte teve
**215 testes passando e uma falha**, com os mesmos 11 avisos de depreciação:
`tests/test_janelas_integracao.py::test_segunda_execucao_nao_reprocessa_o_que_ja_entrou`.
Os seis testes da correção foram repetidos e passaram em 2,68 s.

A falha foi reproduzida também no snapshot da PR #62 sem o patch da #60
(`PYTHONPATH` apontando para seu src): quatro testes de janelas passaram e o
mesmo teste falhou, devolvendo as mesmas duas vistorias sem itens de 2019 e
2024. Os arquivos janelas.py e test_janelas_integracao.py são idênticos nessa
base e na main atualizada. Portanto, essa falha não é introduzida pela correção
do slot. O estado por janelas identifica incorporados pelos itens exportados,
mas essas vistorias não têm itens e voltam como candidatas. Esse comportamento
fica registrado para investigação separada, sem alterar o escopo desta PR.

As contagens anteriores de 201 testes e do snapshot integrado de 212 testes
referem-se à base anterior à integração da #62 e foram preservadas como
histórico, não como alegação de suíte integral passando na base mais recente.
