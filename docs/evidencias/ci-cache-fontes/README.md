# Reduzir o tempo do CI com cache de fontes

## Resultado medido em 24/11/2026

| Cenário | Tempo do job de validação | Conclusão |
| --- | ---: | --- |
| [Referência anterior, PR #103](https://github.com/daviegito/bd2_2026_2_eduinfra/actions/runs/45468098049/attempts/1) | 19 min 26 s | aprovado |
| [Experimento sem ZIPs reaproveitados](https://github.com/daviegito/bd2_2026_2_eduinfra/actions/runs/45473371890/attempts/1) | 16 min 14 s | aprovado |
| [Experimento com cache funcional](https://github.com/daviegito/bd2_2026_2_eduinfra/actions/runs/45479758756/attempts/1) | **5 min 29 s** | aprovado |

Na comparação sem/com cache, a economia observada foi de **10 min 45 s (66,2%)**.
O preparo das fontes passou de **559 s para 13 s**, mais **3 s** para restaurar os
ZIPs. A redução em relação à referência anterior foi de 71,8%.

São **uma amostra válida por cenário**, com variação de rede e runner. As revisões
diferem por correções na preparação dos diretórios e no acesso ao workflow pelo
novo teste. Um `git diff` confirmou que leitores, modelos, scripts de transformação,
Compose e dependências não mudaram entre os casos frio e quente. Não se afirma
que são duas execuções do mesmo commit nem se garante essa duração em todo runner.

Os SHA-256 dos dois ZIPs e os tamanhos dos CSVs coincidiram. Carga, CDC, 13 modelos
e 26 testes do dbt, seis testes de auditoria, geração do painel e compilação do site
continuaram aprovados. O resultado não usa banco, Silver, Delta ou Gold pré-construídos.

[Comparação consolidada](comparacao.json), [referência](baseline-103.json),
[execução fria](frio.json), [fontes frias](frio-fontes.json),
[execução quente](quente.json) e [fontes quentes](quente-fontes.json).
Os dados de preparo foram extraídos dos registros estruturados dos logs oficiais.

## Mudança implementada

1. Criar os diretórios do cache como usuário do runner antes dos contêineres.
2. Construir imagens e executar os testes rápidos do painel e do preparo do cache.
3. Restaurar **somente os dois ZIPs públicos**, sem dados já processados.
4. Baixar os ausentes e extrair os CSVs com as funções normais do projeto, sem iniciar PostgreSQL.
5. Salvar os ZIPs antes da integração: falhas posteriores não exigem novo download.
6. Executar carga e transformação completas sobre banco novo, com auditoria e validação do painel.

`docker-compose.ci.yml` acrescenta o mesmo bind em `/dados/bruto` à carga e à
transformação. Os demais caminhos continuam em `dados_censo`, e PostgreSQL mantém
seu próprio volume. O override é habilitado por `COMPOSE_FILE` apenas no job de CI.
A etapa de testes monta os workflows como somente leitura para a regressão de permissões.

A chave do cache inclui sistema operacional, anos, revisão manual e hash da
configuração/download/Compose. Não há fallback para versões diferentes. Aumente
`REVISAO_CACHE_FONTES` se o INEP republicar uma fonte do mesmo ano/URL.
Os CSVs são extraídos em cada runner novo; todo o processamento do código do PR
continua sendo executado. ZIP e CSV ficam fora do Git e do contexto de build Docker.

Cache ausente ou indisponível mantém o download normal. Somente restaurar/salvar
cache têm `continue-on-error`; preparo, Gold e testes continuam bloqueando o job
quando falham. O artefato `tempos-fontes` registra reaproveitamento, tempo de preparo,
tamanhos e SHA-256, sem incluir credenciais nem os CSVs.

## Como reproduzir

1. Use uma revisão de cache ainda não existente para medir o caso frio.
2. Execute o workflow e preserve tempos e logs da tentativa completa.
3. Reexecute a mesma revisão; confirme `cache-hit`, ambos os `zip_reutilizado: true`
   e os mesmos SHA-256. O GitHub pode recriar os artefatos ao reexecutar um run;
   preserve os resultados da primeira tentativa antes disso.
4. Compare o **tempo total**, incluindo transferência do cache, e confirme novamente
   a integração completa. Tentativas canceladas ou com cache não restaurado não são
   amostras de sucesso com cache.

O cache segue o [escopo definido pelo GitHub](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching):
um PR reutiliza seu próprio cache e o da base; caches de outro PR não são compartilhados
automaticamente. Após integração, uma execução na `main` precisa semear o cache comum.
Os demais PRs precisam incorporar o novo workflow para usufruir da otimização.

## Verificação local

```bash
uv run --no-sync pytest tests/test_preparar_fontes_ci.py tests/consumo/test_painel.py -q
```

Um servidor HTTP local entrega ZIPs de teste. A execução fria faz duas requisições;
outra pasta, recebendo **apenas os ZIPs**, gera os mesmos CSVs sem novas requisições.
Os hashes são conferidos contra os bytes servidos. Um ZIP sem `RESULTADOS_2024.csv`
continua falhando antes de produzir relatório de sucesso.

O teste de permissões usa dois usuários e a ordem real do workflow. Ele reproduz
o bind criado como root e verifica a restauração pelo runner após a preparação
antecipada. Essa simulação exige o contêiner Linux/root usado pelo CI; fora dele o
caso é ignorado com motivo explícito.

Foram **16 testes aprovados e quatro ignorados por falta de Node**, tanto na bancada
quanto na execução quente. Uma tentativa de executar toda a suíte local parou na
coleta de `tests/test_transformacao_property.py`, porque a imagem não tem o extra
opcional Hypothesis. Não foram alteradas dependências ou testes da #99 para contornar isso.

Também foram extraídos os ZIPs nacionais já existentes, copiados de volume somente
de leitura, com rede desativada: Censo 0,602 s e ENEM 8,052 s. O
[relatório local](zip-real-local.json) não inclui baixar o cache, hash, Silver, Gold
ou site; não representa o tempo total do CI.

## Diagnósticos excluídos da comparação

- Tentativa 2 do run `45473371890`: o cache existia, mas o `tar` recebeu
  `Permission denied` porque o Docker criou o bind como root. O fallback baixou
  novamente; a tentativa foi cancelada. Corrigido criando os diretórios como runner.
- Run `45478583454`: o novo teste não encontrava o workflow na imagem da aplicação.
  A falha foi reproduzida com o layout real e corrigida com bind somente de leitura.
  O job parou nos testes rápidos, antes da carga nacional.

Detalhes em [diagnosticos.json](diagnosticos.json). A execução quente válida foi
feita após ambas as correções e registrou restauração bem-sucedida, sem novo download.
