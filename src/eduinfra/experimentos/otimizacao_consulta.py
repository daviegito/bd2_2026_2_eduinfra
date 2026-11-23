"""Consulta otimizada a partir do plano de execução (issue #51): captura e compara os planos.

Só lê, e exige a Gold já construída por construir-gold.
"""

import json
import statistics
from dataclasses import dataclass
from pathlib import Path

import psycopg

TABELA_DA_GOLD = "gold.fato_infra_escola_ano"

CONSULTA_NA_VIEW = """
select e.tp_dependencia, count(*) as escolas
from escola_estado_atual s
join escola e using (co_entidade)
where s.item_codigo = 'biblioteca' and s.situacao = 'inexistente'
group by e.tp_dependencia
order by e.tp_dependencia
"""

# Mesma resposta da view: a linha mais recente de cada escola. Com DISTINCT ON, o plano percorre a
# chave primária e visita a tabela a cada linha (373 mil blocos, contra 7 mil deste anti-join).
CONSULTA_NA_GOLD = """
select e.tp_dependencia, count(*) as escolas
from gold.fato_infra_escola_ano f
join gold.dim_escola e using (co_entidade)
where f.biblioteca is false
  and not exists (
      select 1 from gold.fato_infra_escola_ano posterior
      where posterior.co_entidade = f.co_entidade and posterior.ano > f.ano
  )
group by e.tp_dependencia
order by e.tp_dependencia
"""

# A pergunta do painel, no grão do projeto. A resposta difere da view só pelas vistorias posteriores
# a 2024, que na base de desenvolvimento são as de teste.
CONSULTA_DO_PAINEL = """
select e.tp_dependencia, count(*) as escolas
from gold.fato_infra_escola_ano f
join gold.dim_escola e using (co_entidade)
where f.ano = 2024 and f.biblioteca is false
group by e.tp_dependencia
order by e.tp_dependencia
"""

CONSULTAS = {"antes": CONSULTA_NA_VIEW, "depois": CONSULTA_NA_GOLD, "painel": CONSULTA_DO_PAINEL}

# O diagnóstico mede o tempo de cada nó e a série não, porque ler o relógio em cada linha pesa
# no total. Por isso os tempos dos dois modos nunca entram na mesma mediana.
OPCOES_DO_DIAGNOSTICO = "analyze, buffers, verbose, settings"
OPCOES_DA_SERIE = "analyze, buffers, timing off"

# A série só compara execuções depois do aquecimento, para a queda de leitura em disco não
# ser atribuída à mudança quando vem do cache.
AQUECIMENTOS = 2
FATOR_DE_ERRO_DE_ESTIMATIVA = 10


class GoldAusente(Exception):
    pass


@dataclass(frozen=True)
class Execucao:
    tempo_ms: float
    leitura_em_disco_ms: float
    blocos_em_cache: int
    blocos_lidos: int
    temporarios_lidos: int
    temporarios_escritos: int


@dataclass(frozen=True)
class ErroDeEstimativa:
    no: str
    linhas_estimadas: int
    linhas_reais: int
    fator: float


@dataclass(frozen=True)
class Medicao:
    plano_json: list
    plano_texto: str
    serie: list[Execucao]
    erros_de_estimativa: list[ErroDeEstimativa]
    linhas: list[tuple]


@dataclass(frozen=True)
class Comparacao:
    antes: Medicao
    depois: Medicao
    painel: Medicao

    @property
    def mesmo_resultado(self) -> bool:
        return self.antes.linhas == self.depois.linhas

    def medicoes(self) -> dict[str, Medicao]:
        return {"antes": self.antes, "depois": self.depois, "painel": self.painel}


def resumir_execucao(plano: list) -> Execucao:
    # Os buffers de um nó já somam os dos filhos: só a raiz dá o total sem contar duas vezes.
    raiz = plano[0]["Plan"]
    return Execucao(
        tempo_ms=plano[0]["Execution Time"],
        leitura_em_disco_ms=raiz.get("I/O Read Time", 0.0),
        blocos_em_cache=raiz["Shared Hit Blocks"],
        blocos_lidos=raiz["Shared Read Blocks"],
        temporarios_lidos=raiz["Temp Read Blocks"],
        temporarios_escritos=raiz["Temp Written Blocks"],
    )


def erros_de_estimativa(plano: list, fator_minimo: float = FATOR_DE_ERRO_DE_ESTIMATIVA) -> list[ErroDeEstimativa]:
    erros = []
    pendentes = [plano[0]["Plan"]]
    while pendentes:
        no = pendentes.pop()
        pendentes.extend(no.get("Plans", []))
        estimadas, reais = no["Plan Rows"], no["Actual Rows"]
        fator = max(estimadas, reais) / max(min(estimadas, reais), 1)
        if fator >= fator_minimo:
            erros.append(ErroDeEstimativa(_nome_do_no(no), estimadas, int(reais), fator))
    return sorted(erros, key=lambda erro: erro.fator, reverse=True)


def _nome_do_no(no: dict) -> str:
    alvo = no.get("Index Name") or no.get("Relation Name") or no.get("Alias")
    return f"{no['Node Type']} em {alvo}" if alvo else no["Node Type"]


def comparar(dsn: str, repeticoes: int) -> Comparacao:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        if conexao.execute("select to_regclass(%s)", (TABELA_DA_GOLD,)).fetchone()[0] is None:
            raise GoldAusente(f"tabela {TABELA_DA_GOLD} não existe: rode construir-gold antes de comparar")

        # A hipótese do diagnóstico é leitura de tabela, então todos os lados medem o tempo de I/O.
        conexao.execute("set track_io_timing = on")

        linhas = {rotulo: _aquecer(conexao, consulta) for rotulo, consulta in CONSULTAS.items()}
        diagnosticos = {rotulo: _diagnosticar(conexao, consulta) for rotulo, consulta in CONSULTAS.items()}

        # As consultas se alternam para a oscilação da máquina não cair toda sobre uma delas.
        series = {rotulo: [] for rotulo in CONSULTAS}
        for _ in range(repeticoes):
            for rotulo, consulta in CONSULTAS.items():
                series[rotulo].append(resumir_execucao(_explicar_em_json(conexao, consulta, OPCOES_DA_SERIE)))

    return Comparacao(
        **{
            rotulo: Medicao(
                plano_json=diagnosticos[rotulo][0],
                plano_texto=diagnosticos[rotulo][1],
                serie=series[rotulo],
                erros_de_estimativa=erros_de_estimativa(diagnosticos[rotulo][0]),
                linhas=linhas[rotulo],
            )
            for rotulo in CONSULTAS
        }
    )


def _aquecer(conexao: psycopg.Connection, consulta: str) -> list[tuple]:
    linhas = conexao.execute(consulta).fetchall()
    for _ in range(AQUECIMENTOS - 1):
        conexao.execute(consulta).fetchall()
    return linhas


def _diagnosticar(conexao: psycopg.Connection, consulta: str) -> tuple[list, str]:
    plano_json = _explicar_em_json(conexao, consulta, OPCOES_DO_DIAGNOSTICO)
    plano_texto = "\n".join(
        linha[0] for linha in conexao.execute(f"explain ({OPCOES_DO_DIAGNOSTICO}) {consulta}").fetchall()
    )
    return plano_json, plano_texto


def _explicar_em_json(conexao: psycopg.Connection, consulta: str, opcoes: str) -> list:
    return conexao.execute(f"explain ({opcoes}, format json) {consulta}").fetchone()[0]


def gravar_planos(comparacao: Comparacao, pasta: Path) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    for rotulo, medicao in comparacao.medicoes().items():
        (pasta / f"{rotulo}.json").write_text(json.dumps(medicao.plano_json, indent=2) + "\n", encoding="utf-8")
        (pasta / f"{rotulo}.txt").write_text(medicao.plano_texto + "\n", encoding="utf-8")
    serie = {rotulo: [vars(execucao) for execucao in medicao.serie] for rotulo, medicao in comparacao.medicoes().items()}
    (pasta / "serie.json").write_text(json.dumps(serie, indent=2) + "\n", encoding="utf-8")
    (pasta / "resumo.txt").write_text(formatar_markdown(comparacao) + "\n", encoding="utf-8")


def formatar_markdown(comparacao: Comparacao) -> str:
    medicoes = comparacao.medicoes()
    linhas = [
        "| Medida (mediana da série, buffers da raiz do plano) | Antes (view) | Depois (Gold) | Painel (Gold, 2024) |",
        "| --- | ---: | ---: | ---: |",
        _linha("Tempo de execução", medicoes, "tempo_ms", lambda valor: f"{_decimal(valor)} ms"),
        _linha("Tempo lendo do disco", medicoes, "leitura_em_disco_ms", lambda valor: f"{_decimal(valor)} ms"),
        _linha("Blocos em cache (shared hit)", medicoes, "blocos_em_cache", _inteiro),
        _linha("Blocos lidos (shared read)", medicoes, "blocos_lidos", _inteiro),
        _linha("Temporários lidos (temp read)", medicoes, "temporarios_lidos", _inteiro),
        _linha("Temporários escritos (temp written)", medicoes, "temporarios_escritos", _inteiro),
        "",
        "Execuções da série, em ms.",
    ]
    linhas += [f"- {rotulo}: {_tempos(medicao)}" for rotulo, medicao in medicoes.items()]
    linhas += [
        "",
        "| Erro de estimativa no diagnóstico | Nó | Estimadas | Reais | Fator |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    linhas += [
        f"| {rotulo} | {erro.no} | {_inteiro(erro.linhas_estimadas)} | {_inteiro(erro.linhas_reais)} | {_decimal(erro.fator)} |"
        for rotulo, medicao in medicoes.items()
        for erro in medicao.erros_de_estimativa
    ]
    linhas += [
        "",
        "A view e a Gold devolveram as mesmas linhas."
        if comparacao.mesmo_resultado
        else "ATENÇÃO: a view e a Gold devolveram linhas diferentes, então a comparação não vale.",
        f"Linhas do painel: {comparacao.painel.linhas}.",
    ]
    return "\n".join(linhas)


def _linha(titulo: str, medicoes: dict[str, Medicao], campo: str, formato) -> str:
    colunas = " | ".join(formato(_mediana(medicao, campo)) for medicao in medicoes.values())
    return f"| {titulo} | {colunas} |"


def _mediana(medicao: Medicao, campo: str) -> float:
    return statistics.median(getattr(execucao, campo) for execucao in medicao.serie)


def _tempos(medicao: Medicao) -> str:
    return ", ".join(_decimal(execucao.tempo_ms) for execucao in medicao.serie)


def _decimal(valor: float) -> str:
    return f"{valor:.1f}".replace(".", ",")


def _inteiro(valor: float) -> str:
    return f"{round(valor):,}".replace(",", ".")
