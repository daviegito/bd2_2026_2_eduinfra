"""Controle de acesso da issue #86: permitidos, negados, recorte de linha e de coluna.

Os testes sem banco verificam a declaração (matriz e migração): se alguém ampliar
um privilégio, a asserção quebra. Os testes de integração executam os quatro casos
contra o PostgreSQL, com os papéis da migração 0010.
"""

import importlib.util
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import errors

from eduinfra import controle_de_acesso, fluxo_cdc
from eduinfra.configuracao import carregar_configuracao

RAIZ = Path(__file__).resolve().parents[1]


def _carregar_migracao():
    caminho = RAIZ / "alembic" / "versions" / "0010_controle_de_acesso.py"
    spec = importlib.util.spec_from_file_location("migracao_0010", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _dsn_como(dsn_base: str, usuario: str, senha: str) -> str:
    partes = urlsplit(dsn_base)
    host = partes.hostname or "localhost"
    porta = f":{partes.port}" if partes.port else ""
    netloc = f"{usuario}:{senha}@{host}{porta}"
    return urlunsplit((partes.scheme, netloc, partes.path, partes.query, partes.fragment))


# --- Declaração: falham se uma permissão for ampliada -----------------------------


def test_recorte_de_coluna_nao_inclui_nome_nem_matricula():
    assert "nome" not in controle_de_acesso.COLUNAS_PERMITIDAS_TECNICO
    assert "matricula" not in controle_de_acesso.COLUNAS_PERMITIDAS_TECNICO


def test_papeis_de_servico_sao_dedicados():
    for papel in controle_de_acesso.PAPEIS_DE_SERVICO:
        assert papel in controle_de_acesso.PAPEIS
        assert papel != os.environ.get("POSTGRES_USER"), "serviço de rotina não pode ser o superusuário"


def test_painel_nao_recebe_escrita_em_nenhum_objeto():
    fonte = (RAIZ / "alembic" / "versions" / "0010_controle_de_acesso.py").read_text(encoding="utf-8")
    for linha in fonte.lower().splitlines():
        if "eduinfra_painel" not in linha:
            continue
        for verbo in ("insert", "update", "delete", "alter role"):
            assert verbo not in linha, f"o painel não pode receber {verbo}: {linha.strip()}"
        assert "entrada_transformacao" not in linha, f"painel não lê entrada: {linha.strip()}"
        assert "monitoramento" not in linha, f"painel não lê monitoramento: {linha.strip()}"
        assert "public." not in linha, f"painel não lê a origem: {linha.strip()}"


def test_tecnico_tem_politica_de_linha_por_uf():
    migracao = _carregar_migracao()
    rls = " ".join(migracao.RLS).lower()
    assert "enable row level security" in rls
    assert "create policy vistoria_fila" in rls
    for papel in ("eduinfra_tecnico_df", "eduinfra_tecnico_sp"):
        assert papel in rls


def test_cdc_tem_replicacao_declarada():
    migracao = _carregar_migracao()
    fonte = (RAIZ / "alembic" / "versions" / "0010_controle_de_acesso.py").read_text(encoding="utf-8")
    assert "alter role eduinfra_cdc with replication" in fonte
    assert "alter role eduinfra_cdc_bancada with replication" in fonte
    assert "alter role eduinfra_carga with noreplication" in fonte
    assert migracao.FILA_POR_UF == {"eduinfra_tecnico_df": "DF", "eduinfra_tecnico_sp": "SP"}


def test_dsn_do_papel_troca_usuario_e_mantem_destino():
    dsn = controle_de_acesso.dsn_do_papel(
        "eduinfra_painel", base="postgresql://admin:segredo@banco:5432/eduinfra"
    )
    partes = psycopg.conninfo.conninfo_to_dict(dsn)
    assert partes["user"] == "eduinfra_painel"
    assert partes["password"] == controle_de_acesso.senha_do_papel("eduinfra_painel")
    assert partes["dbname"] == "eduinfra"
    assert partes["host"] == "banco"
    assert partes["port"] == "5432"


def test_migracao_0010_declara_senhas_independentes_cdc_e_metricas():
    """Garante que a migração 0010 não compartilha senha entre papéis e concede CDC/metricas."""
    conteudo = (RAIZ / "alembic" / "versions" / "0010_controle_de_acesso.py").read_text(encoding="utf-8")

    assert "SENHA_PADRAO =" not in conteudo
    assert "SENHAS_PADRAO =" in conteudo
    for var in (
        "EDUINFRA_SENHA_CARGA",
        "EDUINFRA_SENHA_DBT",
        "EDUINFRA_SENHA_CDC",
        "EDUINFRA_SENHA_CDC_BANCADA",
        "EDUINFRA_SENHA_PAINEL",
        "EDUINFRA_SENHA_ANALISE",
        "EDUINFRA_SENHA_TECNICO_DF",
        "EDUINFRA_SENHA_TECNICO_SP",
        "EDUINFRA_SENHA_ORQUESTRADOR",
    ):
        assert var in conteudo

    assert "grant select on public.escola, public.municipio to eduinfra_cdc" in conteudo
    assert '"metricas": "eduinfra_dbt"' in conteudo


# --- Integração: os quatro casos contra o banco -----------------------------------


@pytest.fixture(scope="module")
def base():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL não definida")
    try:
        with psycopg.connect(url, connect_timeout=5) as conexao:
            papeis = conexao.execute(
                "select count(*) from pg_roles where rolname = any(%s)",
                (list(controle_de_acesso.PAPEIS),),
            ).fetchone()[0]
    except psycopg.Error as erro:
        pytest.fail(f"banco indisponível em DATABASE_URL: {erro}", pytrace=False)
    if papeis != len(controle_de_acesso.PAPEIS):
        pytest.skip("migração 0010 não aplicada: rode `migrar` antes")
    return url


def _conexao(papel, base):
    return psycopg.connect(controle_de_acesso.dsn_do_papel(papel, base), connect_timeout=5)


@pytest.mark.integracao
def test_nenhum_papel_de_servico_e_superusuario(base):
    with psycopg.connect(base) as conexao:
        linhas = conexao.execute(
            "select rolname, rolsuper, rolbypassrls from pg_roles where rolname = any(%s)",
            (list(controle_de_acesso.PAPEIS),),
        ).fetchall()
    atributos = {nome: (superusuario, bypass) for nome, superusuario, bypass in linhas}
    for papel in controle_de_acesso.PAPEIS:
        assert atributos[papel] == (False, False), f"{papel} não pode ser superusuário nem ignorar RLS"


@pytest.mark.integracao
def test_cdc_replica_sem_ser_superusuario(base):
    with psycopg.connect(base) as conexao:
        superusuario, replicacao = conexao.execute(
            "select rolsuper, rolreplication from pg_roles where rolname = 'eduinfra_cdc'"
        ).fetchone()
    assert replicacao and not superusuario


@pytest.mark.integracao
def test_leitura_permitida_do_painel(base):
    with _conexao("eduinfra_painel", base) as conexao:
        total = conexao.execute("select count(*) from gold.dim_escola").fetchone()[0]
    assert total >= 0


@pytest.mark.integracao
def test_escrita_negada_do_painel(base):
    with _conexao("eduinfra_painel", base) as conexao:
        with pytest.raises(errors.InsufficientPrivilege):
            conexao.execute(
                "insert into public.municipio (co_municipio, no_municipio, sg_uf, co_uf) "
                "values (-1, 'tentativa do painel', 'XX', 11)"
            )


@pytest.mark.integracao
def test_dbt_escreve_so_na_gold(base):
    with _conexao("eduinfra_dbt", base) as conexao:
        with pytest.raises(errors.InsufficientPrivilege):
            conexao.execute(
                "insert into public.vistoria (co_entidade, tecnico_id, origem, ocorrido_em) "
                "values (1, 1, 'vistoria_presencial', now())"
            )
        conexao.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conexao.execute("create table entrada_transformacao._teste_dbt (x integer)")
        conexao.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conexao.execute("insert into monitoramento.frescor_gold (medido_em, comando, tabela, gold_construida, sla_s, dentro_do_sla) values (now(), 'x', 'x', true, 0, true)")
        conexao.rollback()
    with psycopg.connect(controle_de_acesso.dsn_do_papel("eduinfra_dbt", base)) as conexao:
        conexao.execute("create table gold._teste_acesso (x integer)")
        conexao.rollback()


@pytest.mark.integracao
def test_linha_de_outra_uf_invisivel(base):
    esperado = {
        uf: _contar_uf(base, uf) for uf in ("DF", "SP")
    }
    if not all(esperado.values()):
        pytest.skip("sem vistorias de DF/SP: rode `ingerir` antes")
    sp_id = _ultima_vistoria_da_uf(base, "SP")

    with _conexao("eduinfra_tecnico_df", base) as df, _conexao("eduinfra_tecnico_sp", base) as sp:
        assert df.execute("select count(*) from public.vistoria").fetchone()[0] == esperado["DF"]
        assert sp.execute("select count(*) from public.vistoria").fetchone()[0] == esperado["SP"]
        assert df.execute("select count(*) from public.vistoria where id = %s", (sp_id,)).fetchone()[0] == 0
        assert sp.execute("select count(*) from public.vistoria where id = %s", (sp_id,)).fetchone()[0] == 1


@pytest.mark.integracao
def test_coluna_negada_para_analise(base):
    with _conexao("eduinfra_analise", base) as conexao:
        conexao.execute("select id, lotacao_uf from public.tecnico limit 1").fetchone()
        for coluna in ("nome", "matricula"):
            with pytest.raises(errors.InsufficientPrivilege):
                conexao.execute(f"select {coluna} from public.tecnico limit 1")
            conexao.rollback()


@pytest.mark.integracao
def test_demonstracao_fecha_os_quatro_casos(base):
    casos = controle_de_acesso.demonstrar(base)
    falhas = [caso.linha() for caso in casos if not caso.ok]
    assert len(casos) == 4
    assert not falhas, "\n".join(falhas)


def _contar_uf(base, uf):
    with psycopg.connect(base) as conexao:
        return conexao.execute(
            """
            select count(*) from public.vistoria v
            join public.escola e on e.co_entidade = v.co_entidade
            join public.municipio m on m.co_municipio = e.co_municipio
            where m.sg_uf = %s
            """,
            (uf,),
        ).fetchone()[0]


def _ultima_vistoria_da_uf(base, uf):
    with psycopg.connect(base) as conexao:
        return conexao.execute(
            """
            select coalesce(max(v.id), 0) from public.vistoria v
            join public.escola e on e.co_entidade = v.co_entidade
            join public.municipio m on m.co_municipio = e.co_municipio
            where m.sg_uf = %s
            """,
            (uf,),
        ).fetchone()[0]


@pytest.mark.integracao
def test_painel_le_so_gold(base):
    with _conexao("eduinfra_painel", base) as conexao:
        conexao.execute("select count(*) from gold.dim_escola").fetchone()
        for consulta in (
            "select count(*) from public.vistoria",
            "select count(*) from entrada_transformacao.enem_escola_ano",
            "select count(*) from monitoramento.frescor_gold",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                conexao.execute(consulta)
            conexao.rollback()


@pytest.mark.integracao
def test_tecnico_so_fila(base):
    if not _contar_uf(base, "DF") or not _contar_uf(base, "SP"):
        pytest.skip("sem vistorias de DF/SP: rode `ingerir` antes")
    with _conexao("eduinfra_tecnico_df", base) as conexao:
        for consulta in (
            "select count(*) from public.escola",
            "select count(*) from public.municipio",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                conexao.execute(consulta)
            conexao.rollback()
        total = conexao.execute("select count(*) from public.fila_vistoria").fetchone()[0]
        fora = conexao.execute("select count(*) from public.fila_vistoria where sg_uf <> 'DF'").fetchone()[0]
        assert total > 0
        assert fora == 0


@pytest.mark.integracao
def test_carga_sem_replication_e_com_gold_leitura(base):
    with psycopg.connect(base) as conexao:
        replicacao = conexao.execute(
            "select rolreplication from pg_roles where rolname = 'eduinfra_carga'"
        ).fetchone()[0]
    assert replicacao is False
    with _conexao("eduinfra_carga", base) as conexao:
        conexao.execute("select count(*) from gold.dim_escola").fetchone()
        with pytest.raises(errors.InsufficientPrivilege):
            conexao.execute("create table gold._teste_carga (x integer)")
        conexao.rollback()
        conexao.execute("select count(*) from public.vistoria").fetchone()


@pytest.mark.integracao
def test_cdc_nao_escreve_origem(base):
    with _conexao("eduinfra_cdc", base) as conexao:
        with pytest.raises(errors.InsufficientPrivilege):
            conexao.execute(
                "insert into public.vistoria (co_entidade, tecnico_id, origem, ocorrido_em) "
                "values (1, 1, 'vistoria_presencial', now())"
            )
        conexao.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conexao.execute("select count(*) from gold.dim_escola")
        conexao.rollback()


@pytest.mark.integracao
def test_senhas_independentes_impedem_troca_de_papel(base):
    """Reutilizar a senha do painel ou de outro técnico deve falhar na autenticação."""
    senha_painel = os.environ.get("EDUINFRA_SENHA_PAINEL", "senha_local_painel")
    senha_tecnico_df = os.environ.get("EDUINFRA_SENHA_TECNICO_DF", "senha_local_tecnico_df")

    dsn_forjado_orquestrador = _dsn_como(base, "eduinfra_orquestrador", senha_painel)
    with pytest.raises(psycopg.OperationalError):
        with psycopg.connect(dsn_forjado_orquestrador):
            pass

    dsn_forjado_tecnico_sp = _dsn_como(base, "eduinfra_tecnico_sp", senha_tecnico_df)
    with pytest.raises(psycopg.OperationalError):
        with psycopg.connect(dsn_forjado_tecnico_sp):
            pass


@pytest.mark.integracao
def test_cdc_passar_uma_vez_com_papel_de_rotina(base):
    """Consumo real do CDC com eduinfra_cdc deve ler escola/município sem SQLSTATE 42501."""
    config = carregar_configuracao()
    senha_carga = os.environ.get("EDUINFRA_SENHA_CARGA", "senha_local_carga")
    senha_cdc = os.environ.get("EDUINFRA_SENHA_CDC", "senha_local_cdc")

    dsn_carga = _dsn_como(base, "eduinfra_carga", senha_carga)
    dsn_cdc = _dsn_como(base, "eduinfra_cdc", senha_cdc)
    config_cdc = replace(config, dsn=dsn_cdc, dsn_cdc=dsn_cdc)

    fluxo_cdc.preparar_slot(config_cdc)

    co_municipio = 9999991
    co_entidade = 99999991
    vistoria_id: int | None = None

    try:
        with psycopg.connect(dsn_carga, autocommit=True) as conn:
            conn.execute(
                """
                insert into public.municipio (co_municipio, no_municipio, sg_uf, co_uf)
                values (%s, 'Municipio Teste CDC', 'DF', 53)
                on conflict (co_municipio) do nothing
                """,
                (co_municipio,),
            )
            conn.execute(
                                """
                                insert into public.escola (
                                    co_entidade,
                                    no_entidade,
                                    co_municipio,
                                    tp_dependencia
                                )
                                values (%s, 'Escola Teste CDC', %s, 3)
                                on conflict (co_entidade) do nothing
                                """,
                                (co_entidade, co_municipio),
            )
            item = conn.execute("select codigo from public.item_infraestrutura limit 1").fetchone()
            if item is None:
                pytest.skip("Catálogo public.item_infraestrutura vazio para testar evento CDC.")

            vistoria_id = conn.execute(
                """
                insert into public.vistoria (co_entidade, origem, ocorrido_em)
                values (%s, 'teste_cdc_rotina', %s)
                returning id
                """,
                (co_entidade, datetime.now(timezone.utc)),
            ).fetchone()[0]
            conn.execute(
                """
                insert into public.vistoria_item (vistoria_id, item_codigo, situacao)
                values (%s, %s, 'existente')
                """,
                (vistoria_id, item[0]),
            )

        resumo = fluxo_cdc.passar_uma_vez(config_cdc)
        assert resumo is not None
    finally:
        if vistoria_id is not None:
            with psycopg.connect(dsn_carga, autocommit=True) as conn:
                conn.execute("delete from public.vistoria_item where vistoria_id = %s", (vistoria_id,))
                conn.execute("delete from public.vistoria where id = %s", (vistoria_id,))


@pytest.mark.integracao
def test_criacao_de_slot_exige_replicacao_e_separa_da_linha_de_base(base):
    """carga e orquestrador não criam slot; após preparo por eduinfra_cdc, linha de base roda."""
    config = carregar_configuracao()
    senha_carga = os.environ.get("EDUINFRA_SENHA_CARGA", "senha_local_carga")
    senha_orq = os.environ.get("EDUINFRA_SENHA_ORQUESTRADOR", "senha_local_orquestrador")
    senha_cdc = os.environ.get("EDUINFRA_SENHA_CDC", "senha_local_cdc")

    dsn_carga = _dsn_como(base, "eduinfra_carga", senha_carga)
    dsn_orq = _dsn_como(base, "eduinfra_orquestrador", senha_orq)
    dsn_cdc = _dsn_como(base, "eduinfra_cdc", senha_cdc)
    slot_teste = "eduinfra_slot_teste_acesso"

    def _remover_slot_se_existir() -> None:
        with psycopg.connect(dsn_cdc, autocommit=True) as conn:
            existe = conn.execute(
                "select 1 from pg_replication_slots where slot_name = %s",
                (slot_teste,),
            ).fetchone()
            if existe:
                conn.execute("select pg_drop_replication_slot(%s)", (slot_teste,))

    _remover_slot_se_existir()
    try:
        for dsn_sem_replicacao in (dsn_carga, dsn_orq):
            with pytest.raises(errors.InsufficientPrivilege):
                with psycopg.connect(dsn_sem_replicacao, autocommit=True) as conn:
                    conn.execute(
                        "select pg_create_logical_replication_slot(%s, 'pgoutput')",
                        (slot_teste,),
                    )

        config_cdc = replace(config, dsn=dsn_cdc, dsn_cdc=dsn_cdc)
        fluxo_cdc.preparar_slot(config_cdc, slot=slot_teste)

        config_carga_sem_cdc = replace(config, dsn=dsn_carga, dsn_cdc=None)
        resultado = fluxo_cdc.publicar_linha_de_base(
            config_carga_sem_cdc, forcar=False, slot=slot_teste
        )
        assert resultado is not None
    finally:
        _remover_slot_se_existir()


@pytest.mark.integracao
def test_permissoes_schema_metricas(base):
    """Orquestrador/dbt escrevem em metricas; painel apenas lê e não cria nem insere."""
    senha_orq = os.environ.get("EDUINFRA_SENHA_ORQUESTRADOR", "senha_local_orquestrador")
    senha_painel = os.environ.get("EDUINFRA_SENHA_PAINEL", "senha_local_painel")

    dsn_orq = _dsn_como(base, "eduinfra_orquestrador", senha_orq)
    dsn_painel = _dsn_como(base, "eduinfra_painel", senha_painel)

    try:
        with psycopg.connect(dsn_orq, autocommit=True) as conn:
            conn.execute("create table if not exists metricas._teste_acesso (id int primary key)")
            conn.execute("insert into metricas._teste_acesso (id) values (1) on conflict do nothing")

        with psycopg.connect(dsn_painel, autocommit=True) as conn:
            valor = conn.execute("select id from metricas._teste_acesso where id = 1").fetchone()
            assert valor == (1,)

            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute("insert into metricas._teste_acesso (id) values (2)")

            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute("create table metricas._nao_permitido (id int)")
    finally:
        with psycopg.connect(dsn_orq, autocommit=True) as conn:
            conn.execute("drop table if exists metricas._teste_acesso")
