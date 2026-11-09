"""Cobertura da junção entre o ENEM e o Censo: CO_ESCOLA contra CO_ENTIDADE.

Lê as duas Silver publicadas. Do lado do Censo entram só as escolas que a Silver
dele guarda (em atividade e com rede e localização informadas); se o CSV bruto
do Censo estiver em disco, as escolas que não juntam são procuradas nele para
separar "fora da Silver do Censo" de "ausente do Censo".
"""

from dataclasses import dataclass
from pathlib import Path

import duckdb

from eduinfra.configuracao import Configuracao
from eduinfra.enem import ARQUIVO_DESEMPENHO, ARQUIVO_RESUMO, MINIMO_DE_PARTICIPANTES, particao_enem
from eduinfra.microdados import NOME_ARQUIVO_ESCOLAS
from eduinfra.transformacao import particao_silver

# Limites inferiores das faixas de participantes por escola.
FAIXAS = (1, 2, 5, MINIMO_DE_PARTICIPANTES, 30, 100)


@dataclass(frozen=True)
class Faixa:
    rotulo: str
    escolas: int
    participantes: int
    escolas_no_censo: int


@dataclass(frozen=True)
class Cobertura:
    ano_enem: int
    ano_censo: int
    participantes: int
    participantes_com_escola: int
    escolas: int
    escolas_mascaradas: int
    participantes_em_escolas_mascaradas: int
    escolas_no_censo: int
    participantes_em_escolas_no_censo: int
    escolas_fora_da_silver_do_censo: int | None
    faixas: list[Faixa]

    @property
    def escolas_com_codigo_real(self) -> int:
        return self.escolas - self.escolas_mascaradas


def medir(configuracao: Configuracao) -> Cobertura:
    enem = particao_enem(configuracao)
    desempenho = _exigir(enem / ARQUIVO_DESEMPENHO, "rode ingerir-enem antes")
    resumo = _exigir(enem / ARQUIVO_RESUMO, "rode ingerir-enem antes")
    escolas_censo = _exigir(particao_silver(configuracao) / "escola.parquet", "rode ingerir antes")

    conexao = duckdb.connect()
    try:
        conexao.execute(
            f"""
            create view juncao as
            select d.co_escola, d.participantes, d.poucos_participantes as mascarada, c.co_entidade is not null as no_censo
            from read_parquet('{desempenho.as_posix()}', hive_partitioning=false) d
            left join read_parquet('{escolas_censo.as_posix()}', hive_partitioning=false) c
              on c.co_entidade = d.co_escola
            """
        )
        participantes, com_escola = conexao.execute(
            f"select participantes, participantes_com_escola from read_parquet('{resumo.as_posix()}', hive_partitioning=false)"
        ).fetchone()
        escolas, mascaradas, em_mascaradas, no_censo, em_no_censo = conexao.execute(
            """
            select count(*),
                   count(*) filter (where mascarada),
                   coalesce(sum(participantes) filter (where mascarada), 0),
                   count(*) filter (where no_censo),
                   coalesce(sum(participantes) filter (where no_censo), 0)
            from juncao
            """
        ).fetchone()

        return Cobertura(
            ano_enem=configuracao.ano_enem,
            ano_censo=configuracao.ano_censo,
            participantes=participantes,
            participantes_com_escola=com_escola,
            escolas=escolas,
            escolas_mascaradas=mascaradas,
            participantes_em_escolas_mascaradas=em_mascaradas,
            escolas_no_censo=no_censo,
            participantes_em_escolas_no_censo=em_no_censo,
            escolas_fora_da_silver_do_censo=_fora_da_silver(conexao, configuracao),
            faixas=_faixas(conexao),
        )
    finally:
        conexao.close()


def _exigir(arquivo: Path, dica: str) -> Path:
    if not arquivo.exists():
        raise RuntimeError(f"{arquivo} não encontrado: {dica}")

    return arquivo


def _fora_da_silver(conexao: duckdb.DuckDBPyConnection, configuracao: Configuracao) -> int | None:
    """Escolas de código real que não juntam mas existem no CSV do Censo (paralisadas, por exemplo)."""
    bruto = configuracao.diretorio_dados / "bruto" / NOME_ARQUIVO_ESCOLAS.format(ano=configuracao.ano_censo)
    if not bruto.exists():
        return None

    return conexao.execute(
        f"""
        select count(*)
        from juncao j
        where not j.no_censo and not j.mascarada
          and j.co_escola in (
              select try_cast(CO_ENTIDADE as integer)
              from read_csv('{bruto.as_posix()}', delim=';', header=true, encoding='latin-1', all_varchar=true)
          )
        """
    ).fetchone()[0]


def _faixas(conexao: duckdb.DuckDBPyConnection) -> list[Faixa]:
    limites = [*FAIXAS, None]
    casos = "\n".join(
        f"when participantes < {fim} then {indice}" for indice, fim in enumerate(limites[1:-1])
    )
    linhas = conexao.execute(
        f"""
        select case {casos} else {len(FAIXAS) - 1} end as faixa,
               count(*), sum(participantes), count(*) filter (where no_censo)
        from juncao
        group by 1
        order by 1
        """
    ).fetchall()

    return [
        Faixa(_rotulo(limites[indice], limites[indice + 1]), escolas, participantes, no_censo)
        for indice, escolas, participantes, no_censo in linhas
    ]


def _rotulo(inicio: int, fim: int | None) -> str:
    if fim is None:
        return f"{inicio} ou mais"
    if fim - inicio == 1:
        return str(inicio)

    return f"{inicio} a {fim - 1}"


def _pct(parte: int, todo: int) -> str:
    return f"{100 * parte / todo:.1f}%".replace(".", ",") if todo else "—"


def _n(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def formatar_markdown(cobertura: Cobertura) -> str:
    c = cobertura
    reais = c.escolas_com_codigo_real
    em_reais = c.participantes_com_escola - c.participantes_em_escolas_mascaradas
    linhas = [
        f"## Cobertura da junção ENEM {c.ano_enem} × Censo Escolar {c.ano_censo}",
        "",
        "| Medida | Valor | % |",
        "| --- | ---: | ---: |",
        f"| Participantes no arquivo de resultados | {_n(c.participantes)} | 100% |",
        f"| Participantes com escola informada | {_n(c.participantes_com_escola)} | {_pct(c.participantes_com_escola, c.participantes)} |",
        f"| Escolas distintas no ENEM | {_n(c.escolas)} | 100% |",
        f"| Escolas com código mascarado (menos de {MINIMO_DE_PARTICIPANTES} participantes) | {_n(c.escolas_mascaradas)} | {_pct(c.escolas_mascaradas, c.escolas)} |",
        f"| Escolas encontradas no Censo, sobre todas as do ENEM | {_n(c.escolas_no_censo)} | {_pct(c.escolas_no_censo, c.escolas)} |",
        f"| Escolas encontradas no Censo, sobre as de código real | {_n(c.escolas_no_censo)} | {_pct(c.escolas_no_censo, reais)} |",
        f"| Participantes com escola cuja escola junta com o Censo | {_n(c.participantes_em_escolas_no_censo)} | {_pct(c.participantes_em_escolas_no_censo, c.participantes_com_escola)} |",
        f"| Participantes de escola com código real cuja escola junta | {_n(c.participantes_em_escolas_no_censo)} | {_pct(c.participantes_em_escolas_no_censo, em_reais)} |",
    ]
    if c.escolas_fora_da_silver_do_censo is not None:
        linhas.append(
            f"| Escolas de código real que não juntam, mas existem no CSV do Censo | {_n(c.escolas_fora_da_silver_do_censo)} | {_pct(c.escolas_fora_da_silver_do_censo, reais)} |"
        )

    linhas += [
        "",
        "### Escolas por número de participantes",
        "",
        "| Participantes por escola | Escolas | Participantes | Encontradas no Censo |",
        "| --- | ---: | ---: | ---: |",
        *(
            f"| {f.rotulo} | {_n(f.escolas)} | {_n(f.participantes)} | {_n(f.escolas_no_censo)} ({_pct(f.escolas_no_censo, f.escolas)}) |"
            for f in c.faixas
        ),
    ]

    return "\n".join(linhas)
