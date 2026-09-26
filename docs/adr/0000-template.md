# NNNN — [Título: a decisão, em uma frase afirmativa]

- **Status:** proposto | aceito | substituído por [ADR-NNNN] | revogado
- **Data:** AAAA-MM-DD
- **Decisores:** [quem participou]

## Contexto

[Que problema motivou esta decisão?

Caracterize a carga com números do seu domínio: volume, taxa de escrita e de
leitura, cardinalidade, padrão de acesso, latência tolerada, sazonalidade.
"Precisamos escalar" não é caracterização.

Explicite as restrições não funcionais: consistência exigida, disponibilidade,
custo, requisitos legais, competência da equipe, licenciamento. Competência da
equipe é restrição legítima — escreva-a.]

## Alternativas consideradas

### A. Opção nula — continuar como está

[Obrigatória. Por que ela é viável, e por que não foi escolhida — se não foi.
Tratá-la como espantalho trava o ADR na faixa 5–6.]

### B. [Alternativa]

[O que ela oferece. O que ela cobra.]

### C. [Alternativa]

[Idem.]

## Medição

[O que foi medido, com que dado, em que condição.

Precisa ser reproduzível por terceiro: script no repositório, dado de entrada
disponível, comando documentado. Benchmark sintético genérico não conta —
use dado do próprio domínio.]

Como reproduzir:

```
[comando]
```

| Alternativa | [Métrica 1] | [Métrica 2] | [Métrica 3] |
|---|---|---|---|
| A — opção nula | | | |
| B | | | |
| C | | | |

## Decisão

[Escolhemos X.]

## Consequências

**O que ganhamos:**

[...]

**O que perdemos:**

[Seja específico. Esta seção é a que separa a faixa 7–8 da 9–10.
"Aumento de complexidade" é genérico e não conta — diga qual complexidade,
onde ela aparece e quem paga por ela.]

**O que se torna irreversível:**

[Migrar de volta custa o quê? Em tempo, em dado, em reescrita?]

## Gatilho de revisão

[Sob qual métrica esta decisão deixa de valer.

Com número e limiar: "revisar se o p95 da consulta passar de 400 ms" ou
"revisar se o volume diário passar de 2 milhões de linhas". Sem gatilho,
a decisão vira dogma e ninguém a revisita.]
