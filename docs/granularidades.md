### Granularidade do fato

Ano medido: 2024. Escolas na dimensão: 181.065.

| Fato | Grão | Linhas | Tamanho com índices | Mediana | Mínimo | Máximo |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Fino | uma linha por escola, por item, por vistoria | 3.078.105 | 699,8 MB | 449,9 ms | 381,6 ms | 628,3 ms |
| Largo | uma linha por escola por ano, um item por coluna | 181.065 | 24,1 MB | 136,0 ms | 96,9 ms | 148,0 ms |

O grão fino tem 17,0x mais linhas e a consulta leva 3,3x o tempo do largo.
As duas consultas respondem à mesma pergunta (% de escolas com laboratório de ciências, por UF e rede).
Os resultados são idênticos.
