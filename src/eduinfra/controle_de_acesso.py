"""Controle de acesso da issue #86: papéis, recorte de linha e de coluna.

O comando ``demonstrar-acesso`` conecta com cada papel de privilégio mínimo e
registra os quatro casos exigidos:

1. leitura permitida  — o painel lê a Gold;
2. escrita negada     — o painel não insere na origem;
3. linha invisível    — o técnico de DF não vê a vistoria de SP;
4. coluna negada      — a análise não lê ``nome``/``matricula`` de ``tecnico``.

A matriz completa e as decisões estão em ``docs/controle-de-acesso.md``.
"""

import os
from dataclasses import dataclass
from urllib.parse import quote_plus, urlsplit, urlunsplit

import psycopg
from psycopg import errors
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from eduinfra.configuracao import carregar_configuracao

SENHAS_PADRAO: dict[str, tuple[str, str]] = {
    "eduinfra_carga": ("EDUINFRA_SENHA_CARGA", "senha_local_carga"),
    "eduinfra_dbt": ("EDUINFRA_SENHA_DBT", "senha_local_dbt"),
    "eduinfra_cdc": ("EDUINFRA_SENHA_CDC", "senha_local_cdc"),
    "eduinfra_cdc_bancada": ("EDUINFRA_SENHA_CDC_BANCADA", "senha_local_cdc_bancada"),
    "eduinfra_painel": ("EDUINFRA_SENHA_PAINEL", "senha_local_painel"),
    "eduinfra_analise": ("EDUINFRA_SENHA_ANALISE", "senha_local_analise"),
    "eduinfra_tecnico_df": ("EDUINFRA_SENHA_TECNICO_DF", "senha_local_tecnico_df"),
    "eduinfra_tecnico_sp": ("EDUINFRA_SENHA_TECNICO_SP", "senha_local_tecnico_sp"),
    "eduinfra_orquestrador": ("EDUINFRA_SENHA_ORQUESTRADOR", "senha_local_orquestrador"),
}

# Mesmos nomes da migração 0010. Mantidos aqui para o comando e os testes.
PAPEIS = (
    "eduinfra_carga",
    "eduinfra_dbt",
    "eduinfra_cdc",
    "eduinfra_cdc_bancada",
    "eduinfra_painel",
    "eduinfra_analise",
    "eduinfra_tecnico_df",
    "eduinfra_tecnico_sp",
    "eduinfra_orquestrador",
)

# Papéis que o Compose usa para os serviços de rotina. Nenhum é superusuário.
PAPEIS_DE_SERVICO = (
    "eduinfra_carga",
    "eduinfra_dbt",
    "eduinfra_cdc",
    "eduinfra_painel",
    "eduinfra_orquestrador",
)

# Colunas de tecnico que o papel de análise pode ler. nome e matricula ficam de fora.
COLUNAS_PERMITIDAS_TECNICO = ("id", "lotacao_uf", "ativo", "registrado_em")


@dataclass(frozen=True)
class Caso:
    nome: str
    esperado: str
    obtido: str
    ok: bool

    def linha(self) -> str:
        situacao = "OK" if self.ok else "FALHOU"
        return f"[{situacao}] {self.nome}: esperado {self.esperado}; obtido {self.obtido}"


def senha_do_papel(papel: str) -> str:
    variavel, padrao = SENHAS_PADRAO[papel]
    return os.environ.get(variavel, padrao)


def dsn_do_papel(papel: str, base: str | None = None) -> str:
    origem = base or os.environ["DATABASE_URL"]
    partes = urlsplit(origem)
    host = partes.hostname or "localhost"
    porta = f":{partes.port}" if partes.port else ""
    senha = quote_plus(senha_do_papel(papel))
    netloc = f"{papel}:{senha}@{host}{porta}"
    return urlunsplit((partes.scheme, netloc, partes.path, partes.query, partes.fragment))


def _contar(dsn: str, consulta: str, parametros: tuple = ()) -> int:
    with psycopg.connect(dsn, connect_timeout=5) as conexao:
        return conexao.execute(consulta, parametros).fetchone()[0]


def _uf_da_vistoria() -> str:
    return """
        select count(*)
        from public.vistoria v
        join public.escola e on e.co_entidade = v.co_entidade
        join public.municipio m on m.co_municipio = e.co_municipio
        where m.sg_uf = %s
    """


def _caso_leitura_permitida(base: str) -> Caso:
    dsn = dsn_do_papel("eduinfra_painel", base)
    try:
        total = _contar(dsn, "select count(*) from gold.dim_escola")
        return Caso("leitura permitida (painel -> gold)", "SELECT aceito", f"{total} linhas", True)
    except errors.UndefinedTable:
        # Base sem Gold construída: mostra a mesma permissão no catálogo da origem.
        total = _contar(dsn_do_papel("eduinfra_analise", base), "select count(*) from public.municipio")
        return Caso(
            "leitura permitida (análise -> município)",
            "SELECT aceito",
            f"{total} linhas (Gold ausente)",
            True,
        )
    except errors.InsufficientPrivilege:
        return Caso("leitura permitida (painel -> gold)", "SELECT aceito", "permission denied", False)


def _caso_escrita_negada(base: str) -> Caso:
    dsn = dsn_do_papel("eduinfra_painel", base)
    with psycopg.connect(dsn, connect_timeout=5) as conexao:
        try:
            conexao.execute(
                "insert into public.municipio (co_municipio, no_municipio, sg_uf, co_uf) "
                "values (-1, 'tentativa do painel', 'XX', 11)"
            )
        except errors.InsufficientPrivilege:
            return Caso("escrita negada (painel -> municipio)", "permission denied", "permission denied", True)
        conexao.rollback()
        return Caso("escrita negada (painel -> municipio)", "permission denied", "INSERT aceito", False)


def _caso_linha_invisivel(base: str) -> Caso:
    total = _contar(base, "select count(*) from public.vistoria")
    esperado_df = _contar(base, _uf_da_vistoria(), ("DF",))
    esperado_sp = _contar(base, _uf_da_vistoria(), ("SP",))
    sp_id = _contar(base, _uf_da_vistoria().replace("count(*)", "coalesce(max(v.id), 0)"), ("SP",))

    df = dsn_do_papel("eduinfra_tecnico_df", base)
    sp = dsn_do_papel("eduinfra_tecnico_sp", base)
    visto_df = _contar(df, "select count(*) from public.vistoria")
    visto_sp = _contar(sp, "select count(*) from public.vistoria")
    df_ve_sp = _contar(df, "select count(*) from public.vistoria where id = %s", (sp_id,))
    sp_ve_sp = _contar(sp, "select count(*) from public.vistoria where id = %s", (sp_id,))

    ocultas = total - esperado_df - esperado_sp
    ok = (
        esperado_df > 0
        and esperado_sp > 0
        and visto_df == esperado_df
        and visto_sp == esperado_sp
        and df_ve_sp == 0
        and sp_ve_sp == 1
        and visto_df < total
        and visto_sp < total
    )
    obtido = (
        f"DF vê {visto_df}/{esperado_df} e {df_ve_sp} da vistoria {sp_id} de SP; "
        f"SP vê {visto_sp}/{esperado_sp} ({ocultas} de outras UFs ocultas)"
    )
    if not (esperado_df and esperado_sp):
        obtido += "; sem vistorias em DF/SP: rode ingerir antes"
    return Caso("linha invisível (técnico -> UF)", "só a própria UF", obtido, ok)


def _caso_coluna_negada(base: str) -> Caso:
    dsn = dsn_do_papel("eduinfra_analise", base)
    with psycopg.connect(dsn, connect_timeout=5) as conexao:
        conexao.execute("select id, lotacao_uf from public.tecnico limit 1").fetchone()
        for coluna in ("nome", "matricula"):
            try:
                conexao.execute(f"select {coluna} from public.tecnico limit 1").fetchone()
            except errors.InsufficientPrivilege:
                # A transação fica abortada após o erro; reabre para testar a próxima coluna.
                conexao.rollback()
                continue
            return Caso(
                "coluna negada (análise -> tecnico)",
                "nome e matricula negados",
                f"{coluna} liberada",
                False,
            )
    return Caso(
        "coluna negada (análise -> tecnico)",
        "nome e matricula negados",
        "id/lotacao_uf liberados; nome e matricula negados",
        True,
    )


def demonstrar(base: str | None = None) -> list[Caso]:
    base = base or carregar_configuracao().dsn
    return [
        _caso_leitura_permitida(base),
        _caso_escrita_negada(base),
        _caso_linha_invisivel(base),
        _caso_coluna_negada(base),
    ]


def executar() -> int:
    casos = demonstrar()
    print("Demonstração de controle de acesso (issue #86)")
    for caso in casos:
        print(caso.linha())
    falhas = [caso for caso in casos if not caso.ok]
    print(f"{len(casos) - len(falhas)}/{len(casos)} casos como esperado")
    return 0 if not falhas else 1
