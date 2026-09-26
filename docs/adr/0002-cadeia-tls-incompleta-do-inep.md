# ADR 0002 — Completar a cadeia TLS do INEP por AIA em vez de relaxar a verificação

| Campo | Valor |
| --- | --- |
| Status | Aceito |
| Data | 22 de setembro de 2026 |
| Decidido por | Squad EduInfra |
| Entrega | E1 |

## Contexto

A carga precisa baixar os microdados do Censo Escolar direto de
`download.inep.gov.br`, sem passo manual. O download funciona da máquina de
desenvolvimento em Windows e falha dentro do container Linux, sempre, com
`SSL: UNEXPECTED_EOF_WHILE_READING`.

A investigação com `openssl s_client` mostrou a causa: o servidor do INEP
apresenta apenas o certificado folha e não envia o intermediário que o assina
(`RNP ICPEdu GR46 OV TLS CA 2025`). O Schannel do Windows busca o emissor
faltante sozinho pela extensão Authority Information Access; o OpenSSL não faz
isso. Em TLS 1.3 o erro de verificação chega ao cliente como um EOF seco, o que
disfarça a causa real.

Há ainda um segundo problema, independente: o handshake do servidor cai de forma
intermitente mesmo em conexões válidas, o que foi observado tanto no `curl` do
host quanto no container.

## Requisitos e restrições

- A carga precisa rodar em container Linux limpo, sem intervenção manual.
- Nenhuma credencial ou verificação de identidade pode ser afrouxada.
- A solução não pode depender de um arquivo que expire em silêncio.

## Decisão

Montar o contexto TLS do download buscando o certificado intermediário pela URL
declarada na extensão AIA do certificado do servidor, e adicioná-lo ao contexto de
verificação. As âncoras de confiança continuam sendo as raízes públicas já
instaladas no sistema, somadas às do pacote `certifi`. A implementação está em
[tls.py](../../src/eduinfra/tls.py).

Tanto a inspeção do certificado quanto a transferência do arquivo têm novas
tentativas com espera, e o download é retomável por `Range`.

## Alternativa 1 — Desativar a verificação de certificado

- **Prós:** uma linha de código; resolveria imediatamente.
- **Contras:** aceita qualquer certificado, inclusive de um intermediário hostil
  na rede. Transforma um problema de cadeia em um buraco de segurança.
- **Por que não foi escolhida:** o projeto proíbe afrouxar verificação de
  identidade, e o ganho seria apenas de conveniência.

## Alternativa 2 — Versionar o certificado intermediário no repositório

- **Prós:** sem requisição extra em tempo de execução; funciona offline.
- **Contras:** o certificado expira e o repositório passa a carregar um artefato
  que ninguém lembra de renovar. A falha apareceria como erro de TLS meses depois,
  longe da causa.
- **Por que não foi escolhida:** troca um problema resolvido em tempo de execução
  por uma dívida com data de vencimento invisível.

## Evidência

- `openssl s_client -connect download.inep.gov.br:443` dentro do container retorna
  `Verify return code: 21 (unable to verify the first certificate)`, com a cadeia
  apresentada contendo apenas o certificado folha.
- A extensão AIA do certificado aponta para
  `http://secure.globalsign.com/cacert/rnpicpedugr46ovtlsca2025.crt`, e o
  intermediário obtido ali encadeia em `GlobalSign Root R46`, presente no pacote
  de raízes do sistema.
- Com a cadeia completada, a requisição ao arquivo de microdados dentro do
  container passou a responder 200 com 33.829.396 bytes.

## Consequências positivas

- A carga roda em container limpo, cumprindo o critério de reprodutibilidade.
- A verificação de identidade do servidor permanece intacta.
- O mecanismo é genérico: serve para qualquer outra fonte pública com cadeia
  incompleta, situação comum em portais de governo.

## Consequências negativas

- Uma requisição HTTP adicional antes do download.
- Uma conexão de inspeção sem verificação é aberta apenas para ler o certificado
  oferecido. Nenhum dado trafega por ela e nada dali é usado como confiança, mas o
  código precisa deixar isso explícito para não ser lido como descuido.
- Dependência nova de `cryptography` e `certifi`.

## Riscos assumidos

- Se o INEP passar a servir a cadeia completa, este código vira ruído inofensivo
  e ninguém vai lembrar de removê-lo.
- Se a URL de AIA sair do ar, o download falha mesmo com o servidor no ar.

## Como e quando revisitar

- Quando o INEP passar a enviar a cadeia completa, verificável por
  `openssl s_client`, a busca por AIA pode ser removida.
- Se outra fonte pública do projeto apresentar o mesmo problema, a rotina deve
  virar utilitário compartilhado do pipeline, e não algo do módulo de download.

## Histórico de Versões

| Versão | Descrição | Autor | Revisão | Data |
| --- | --- | --- | --- | --- |
| 1.0 | Registro da decisão sobre a cadeia TLS incompleta do INEP | [Artur Mendonça Arruda](https://github.com/ArtyMend07) |  | 22 de setembro de 2026 |
