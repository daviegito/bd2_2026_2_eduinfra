# Experimento: reduzir o tempo do CI com cache de fontes

## Objetivo e referência

Medir se reutilizar ZIPs públicos do Censo/ENEM reduz o tempo do workflow,
conservando carga, CDC, construção/testes da Gold, painel e publicação do mesmo artefato.

Referência: [execução verde do PR #103](https://github.com/daviegito/bd2_2026_2_eduinfra/actions/runs/45468098049),
commit `fad64ad`, em 24/11/2026. A etapa agregada de construir/testar a Gold levou
**1.121 segundos (18 min 41 s)**; geração do painel, 7 s; geração do site, 2 s.
Uma execução anterior gastou aproximadamente 13 min entre iniciar o download do
ENEM e publicar sua Silver, enquanto o dbt levou cerca de 35 s.

## Mudança testada

1. Criar os diretórios do cache com o usuário do runner, antes de qualquer contêiner.
2. Construir imagens e executar os testes existentes do painel e do preparo do cache.
3. Restaurar **somente os dois ZIPs públicos**, sem banco, Silver, Delta ou Gold.
4. Baixar os ausentes e extrair os CSVs com as funções normais do projeto, sem iniciar PostgreSQL.
5. Salvar os ZIPs antes da integração: falhas posteriores não obrigam a baixar tudo novamente.
6. Executar carga e transformação completas sobre banco novo, com os mesmos testes e validação do painel.

`docker-compose.ci.yml` acrescenta o mesmo bind em `/dados/bruto` à carga e à
transformação. Os demais caminhos continuam no volume compartilhado `dados_censo`,
e PostgreSQL continua no seu próprio volume. O Compose de uso local não é alterado;
o override é habilitado por `COMPOSE_FILE` apenas no job de validação.

A chave do cache inclui sistema operacional, anos, revisão manual e hash da
configuração/download. Não há fallback para versões diferentes. Aumente
`REVISAO_CACHE_FONTES` se o INEP republicar uma fonte do mesmo ano/URL.
Os CSVs são extraídos a cada runner novo; o processamento do código do PR continua
sendo executado. ZIP e CSV ficam fora do Git e do contexto de build Docker.

O cache segue o [escopo definido pelo GitHub](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching):
um PR consegue reutilizar o seu próprio cache e o da base; caches de outro PR
não são compartilhados automaticamente. Depois da integração, uma execução na
`main` deve semear o cache comum. Cache ausente ou serviço de cache indisponível continua sendo um caminho válido:
a carga baixa as fontes e executa as mesmas validações. Somente as operações de
restaurar/salvar cache têm `continue-on-error`; a preparação, a Gold e os testes
continuam bloqueando o job em caso de falha.

## Verificação local

```bash
uv run --no-sync pytest tests/test_preparar_fontes_ci.py tests/test_painel.py -q
```

Um servidor HTTP local entrega ZIPs de teste. A primeira execução faz duas
requisições; outra pasta, recebendo **apenas os ZIPs**, gera os mesmos CSVs sem
requisições adicionais. Um teste de permissões executa a ordem real do workflow
com dois usuários: um cria o bind como Docker/root, outro restaura os arquivos como
o runner. Ele falhou antes da preparação antecipada de diretórios e passou depois.
Um ZIP sem `RESULTADOS_2024.csv` continua falhando antes
de produzir o relatório de sucesso.

Foram 16 testes aprovados e quatro ignorados por falta de Node na imagem de bancada.
Uma tentativa de rodar toda a suíte parou na coleta de
`tests/test_transformacao_property.py`, porque a imagem não tem o extra opcional
`hypothesis`. Não foram alteradas dependências ou testes da #99 para contornar isso.

Também foram copiados, de um volume montado **somente para leitura**, os dois ZIPs
reais já existentes. A extração ocorreu em outra pasta, com rede desativada e sem
conexão ao PostgreSQL:

| Fonte | ZIP | CSV extraído | Tempo de extração local |
| --- | ---: | ---: | ---: |
| Censo | 32,3 MiB | 207,8 MiB | 0,602 s |
| ENEM | 501,7 MiB | 1.605,5 MiB | 8,052 s |

[Relatório local e hashes](zip-real-local.json). São tempos da extração, antes do
hash de diagnóstico; não incluem baixar o cache do GitHub, gerar Silver, construir
Gold ou publicar site. Não podem ser apresentados como tempo total do novo CI.

## Como medir no GitHub

Após revisão e autorização de publicação da branch:

1. Executar o workflow uma vez, sem cache desta chave. Registrar tempos de todas as etapas.
2. Reexecutar a mesma revisão. Confirmar `cache-hit: true` e os mesmos SHA-256 dos ZIPs.
3. Comparar **tempo total**, incluindo restaurar/salvar cache, com a execução fria e a referência.
4. Conferir construção/testes da Gold e geração/validação do painel nas duas execuções.

O artefato `tempos-fontes` registra, para cada fonte, reaproveitamento, tempo de
preparo, tamanhos e SHA-256. O workflow mantém as fases separadas para identificar
se o gargalo restante é download, imagem, ingestão ou dbt.

**Medições remotas fria/quente: pendentes.** A redução total ainda não foi comprovada.
A configuração não pula a integração quando ocorre um cache hit.

## Diagnóstico durante a medição remota

A [primeira execução](https://github.com/daviegito/bd2_2026_2_eduinfra/actions/runs/45473371890/attempts/1)
terminou verde em **974 s (16 min 14 s)**, com os dois ZIPs baixados. O cache de
559.910.215 bytes foi salvo antes da integração. Tempos e hashes foram preservados
em `frio.json` e `frio-fontes.json`; a referência anterior está em `baseline-103.json`.

Na tentativa 2, o GitHub encontrou a chave, mas o `tar` não conseguiu escrever em
`.cache/fontes-inep`: o primeiro contêiner havia criado o bind como root. O fallback
voltou a baixar os arquivos. A tentativa foi cancelada para diagnosticar e está
**excluída da comparação de desempenho**. O log registra `Cannot open: Permission denied`
e `Failed to restore`; não foi uma execução com cache funcional.

A correção cria `.cache/fontes-inep/enem` como runner antes dos contêineres. Não altera
os leitores, os modelos, os ZIPs nem a política de testes da Gold. A comparação passa
a registrar explicitamente o commit de cada execução, em vez de alegar uma comparação
fria/quente da mesma revisão quando houve esse ajuste de preparação dos diretórios.
A execução com restauração corrigida ainda precisa ser medida.

A execução `45478583454` parou antes da carga: o novo teste precisava ler o workflow,
que não faz parte da imagem da aplicação. A etapa de testes passa a montar
`.github/workflows` como somente leitura. O problema foi reproduzido localmente
com o layout real da imagem e deixou de ocorrer ao acrescentar esse bind. Essa
execução também está excluída das medições; os diagnósticos estão em `diagnosticos.json`.
