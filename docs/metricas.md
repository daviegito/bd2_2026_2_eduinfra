# Métricas

As métricas da plataforma são definidas em
[`metricas/`](https://github.com/daviegito/bd2_2026_2_eduinfra/tree/main/metricas),
um arquivo por métrica. Qualquer consumidor (painel, consultas, ETL reverso) deve
seguir essa definição.

| Métrica | Descrição | Grão do resultado | Versão |
| --- | --- | --- | --- |
| [`media_enem_escolas`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/media_enem_escolas.yml) | Média das escolas no ENEM, por área | grupo filtrado | 1.0.0 |
| [`diferenca_media_com_sem_item`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/diferenca_media_com_sem_item.yml) | Diferença de nota entre escolas com e sem um item, na mesma rede | rede x item | 1.0.0 |
| [`pct_escolas_por_situacao_do_item`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/pct_escolas_por_situacao_do_item.yml) | Percentual de escolas por situação de um item | grupo filtrado x situação | 1.0.0 |
| [`cobertura_enem_censo`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/cobertura_enem_censo.yml) | Percentual de escolas do ENEM encontradas no Censo | nacional x ano | 1.0.0 |
| [`prioridade_vistoria`](https://github.com/daviegito/bd2_2026_2_eduinfra/blob/main/metricas/prioridade_vistoria.yml) | Escolas públicas a vistoriar primeiro | escola | 0.1.0 (rascunho) |

## Regras comuns

- A média do ENEM é a média das médias escolares: cada escola tem o mesmo peso.
- Grupos com menos de 10 escolas com nota não têm média exibida.
- "Sem o item" significa situação `inexistente`.
- Comparações de nota são feitas dentro da mesma rede de ensino.

## Formato do arquivo

| Campo | Conteúdo |
| --- | --- |
| `nome` | igual ao nome do arquivo |
| `titulo`, `descricao` | o que o número significa |
| `versao`, `status` | `ativa`, `rascunho` ou `descontinuada` |
| `historico` | todas as versões, da mais recente para a mais antiga, com data e o que mudou |
| `dono` | responsável por aprovar mudanças |
| `formula` | SQL de referência sobre a Gold, com um exemplo de filtro |
| `grao` | o que é uma linha nas tabelas lidas |
| `fonte` | tabelas lidas |
| `filtros` | o que pode ser escolhido no painel ou na consulta |
| `supressao` | `minimo` de escolas e a `regra` de quando o valor não é mostrado |
| `consumidores` | quem usa a métrica |

## Versionamento

| Mudança | Versão |
| --- | --- |
| O valor muda para a mesma entrada (fórmula, grão, filtro, supressão) | maior: 1.0.0 → 2.0.0 |
| Novo filtro, sem alterar valores existentes | menor: 1.0.0 → 1.1.0 |
| Só texto | correção: 1.0.0 → 1.0.1 |

Toda nova versão entra no topo do campo `historico` do próprio arquivo.

## Cálculos duplicados

- A contagem de escolas sem biblioteca em `otimizacao_consulta.py` usa
  `biblioteca is false`, que inclui `em_obra` e `inutilizavel`; o painel usa só
  `inexistente`.
- A cobertura ENEM x Censo é calculada em `painel.py`, `cobertura_enem.py` e
  `fontes_transformacao.py`.

## Teste

`tests/test_metricas.py` verifica se cada arquivo tem os campos obrigatórios, se a
versão atual é a primeira do `historico`, se as tabelas da Gold citadas existem e se o mínimo de
supressão do painel é o mesmo da definição. Roda com os testes sem banco e na etapa
`testar_gold` do orquestrador.

```bash
uv run pytest tests/test_metricas.py -q
```
