### Granularidade do fato

Ano medido: 2024. Escolas na dimensão: 181.065.

| Fato | Grão | Linhas | Tamanho com índices | Mediana | Mínimo | Máximo |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Fino | uma linha por escola, por item, por vistoria | 3.078.105 | 545,1 MB | 308,9 ms | 297,4 ms | 657,7 ms |
| Largo | uma linha por escola por ano, um item por coluna | 181.065 | 24,1 MB | 68,2 ms | 61,1 ms | 82,1 ms |

O grão fino tem 17,0x mais linhas e a consulta leva 4,5x o tempo do largo.
As duas consultas respondem à mesma pergunta (% de escolas com laboratório de ciências, por UF e rede).
Os resultados são idênticos.
