"""Papéis com privilégio mínimo, recorte de linha e de coluna (issue #86).

Cada serviço de rotina deixa de usar o superusuário do Compose e passa a
autenticar com o papel da sua função:

- ``eduinfra_carga``     escrita da origem transacional (ingestão) e da camada de
  monitoramento; não replica e não escreve na Gold;
- ``eduinfra_dbt``       transformação: lê a entrada e escreve só na Gold;
- ``eduinfra_cdc``       replicação lógica e leitura das tabelas publicadas;
- ``eduinfra_cdc_bancada`` replicação lógica para as ferramentas de bancada do CDC,
  com escrita em ``tecnico``/``vistoria``/``vistoria_item`` e execução dos
  artefatos gerados; a ingestão de rotina não a usa;
- ``eduinfra_painel``    leitura exclusiva da Gold (e da camada de métricas);
- ``eduinfra_analise``   leitura analítica, sem as colunas pessoais de ``tecnico``;
- ``eduinfra_tecnico_df``/``eduinfra_tecnico_sp`` leem só a fila da sua UF pela
  visão ``public.fila_vistoria``, sem acesso direto a escola/município;
- ``eduinfra_orquestrador`` pertence a ``eduinfra_carga`` e ``eduinfra_dbt``; é o
  dono de ``fonte`` e ``entrada_transformacao`` e não tem ``DATABASE_URL_ADMIN``.

O superusuário continua sendo usado apenas por ``migrar``/``reverter`` (o CLI
troca a conexão por ``DATABASE_URL_ADMIN`` só nesse caminho).

Revision ID: 0010
Revises: 0009
"""

import os

from alembic import op
from psycopg import sql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# Mesma senha local para todos os papéis: o que separa os serviços é o privilégio,
# não a senha. Em produção, sobrescreva EDUINFRA_SENHA_PAPEIS por ambiente.
SENHA_PADRAO = "senha_local_dos_papeis"

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

# UF da fila lida por cada papel de técnico. Em produção, este mapa é alimentado
# a partir de tecnico.lotacao_uf para o papel correspondente ao operador logado.
FILA_POR_UF = {
    "eduinfra_tecnico_df": "DF",
    "eduinfra_tecnico_sp": "SP",
}

# Concessões por objeto. A ordem importa: papéis e esquemas existem antes.
GRANTS = [
    # --- esquema public: cada papel precisa de USAGE para alcançar os objetos ---
    "grant usage on schema public to " + ", ".join(PAPEIS),
    # carga: escrita da origem transacional
    "grant select, insert on public.municipio to eduinfra_carga",
    "grant select, insert on public.escola to eduinfra_carga",
    "grant select, insert on public.tecnico to eduinfra_carga",
    "grant select, insert on public.vistoria to eduinfra_carga",
    "grant select, insert on public.vistoria_item to eduinfra_carga",
    "grant select, insert on public.ingestao_execucao to eduinfra_carga",
    "grant select on public.item_infraestrutura to eduinfra_carga",
    "grant select on public.escola_estado_atual to eduinfra_carga",
    # dbt: lê a entrada congelada, o catálogo e o cadastro do Censo (a opção
    # nula construir-gold monta as dimensões direto da origem); escreve só na Gold.
    "grant select on public.item_infraestrutura to eduinfra_dbt",
    "grant select on public.municipio, public.escola to eduinfra_dbt",
    # cdc: leitura das tabelas publicadas (a replicação exige SELECT)
    "grant select on public.vistoria to eduinfra_cdc",
    "grant select on public.vistoria_item to eduinfra_cdc",
    # cdc_bancada: as ferramentas de bancada criam técnico/vistorias e leem o
    # cadastro para montar a linha de base; a ingestão de rotina usa eduinfra_cdc.
    "grant select, insert on public.tecnico to eduinfra_cdc_bancada",
    "grant select, insert on public.vistoria to eduinfra_cdc_bancada",
    "grant select, insert on public.vistoria_item to eduinfra_cdc_bancada",
    "grant select on public.escola, public.municipio, public.item_infraestrutura to eduinfra_cdc_bancada",
    # painel: só Gold e a camada de métricas; nenhuma tabela da origem
    # analise: leitura analítica; técnico já é lido por coluna (ver COLUNAS_TECNICO)
    "grant select on public.municipio to eduinfra_analise",
    "grant select on public.escola to eduinfra_analise",
    # técnico: lê a fila por RLS e a visão public.fila_vistoria, sem escola/município
    "grant select on public.vistoria to eduinfra_tecnico_df, eduinfra_tecnico_sp",
    "grant select on public.vistoria_item to eduinfra_tecnico_df, eduinfra_tecnico_sp",
]

# Recorte de coluna: o papel de análise não vê nome nem matrícula do técnico.
COLUNAS_TECNICO = ("id", "lotacao_uf", "ativo", "registrado_em")

# Esquemas de trabalho e seus donos. O dbt é dono só da Gold; o orquestrador
# (com os privilégios de carga + dbt) é dono da fonte e da entrada congelada,
# porque é quem prepara a entrada e escreve a fonte antes do build. A carga é
# dona do monitoramento e da comparação.
ESQUEMAS = {
    "gold": "eduinfra_dbt",
    "fonte": "eduinfra_orquestrador",
    "entrada_transformacao": "eduinfra_orquestrador",
    "monitoramento": "eduinfra_carga",
    "comparacao": "eduinfra_carga",
}

# Espelha monitoramento.frescor_gold de eduinfra.frescor, para que a tabela já
# exista com dono e privilégios definidos antes da primeira medição.
CRIAR_FRESCOR = """
create table if not exists monitoramento.frescor_gold (
    id bigint generated always as identity primary key,
    medido_em timestamptz not null,
    comando text not null,
    tabela text not null,
    gold_construida boolean not null,
    vistorias_pendentes integer,
    pendente_desde timestamptz,
    frescor_s double precision,
    sla_s double precision not null,
    dentro_do_sla boolean not null
)
"""

# Mapa papel -> UF lido pela política de linha. Só o dono (migração) escreve.
CRIAR_MAPA_UF = """
create table if not exists public.acesso_uf_papel (
    papel name primary key,
    uf char(2) not null,
    atualizado_em timestamptz not null default now()
)
"""

# SECURITY DEFINER isola o mapa do técnico: ele executa a função, mas não lê
# nem altera a tabela que decide o recorte.
CRIAR_FUNCAO_UF = """
create or replace function public.uf_do_papel(papel name) returns char(2)
language sql stable security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select uf from public.acesso_uf_papel where acesso_uf_papel.papel = $1
$$
"""

# Resolve a UF da escola sem expor escola/município ao técnico: a função é dona
# da consulta (SECURITY DEFINER) e o técnico recebe só EXECUTE.
CRIAR_FUNCAO_UF_DA_ESCOLA = """
create or replace function public.uf_da_escola(co_entidade integer) returns char(2)
language sql stable security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select m.sg_uf
    from public.escola e
    join public.municipio m on m.co_municipio = e.co_municipio
    where e.co_entidade = $1
$$
"""

# Visão da fila por UF: o técnico não lê escola/município direto; a visão é da
# carga (dona das tabelas) e filtra pela mesma UF da política.
CRIAR_VISAO_FILA = """
create or replace view public.fila_vistoria as
select v.id as vistoria_id,
       v.co_entidade,
       e.no_entidade,
       m.no_municipio,
       m.sg_uf,
       v.tecnico_id,
       v.origem,
       v.ocorrido_em,
       v.registrado_em,
       v.observacao,
       count(vi.item_codigo) as itens
from public.vistoria v
join public.escola e on e.co_entidade = v.co_entidade
join public.municipio m on m.co_municipio = e.co_municipio
left join public.vistoria_item vi on vi.vistoria_id = v.id
where m.sg_uf = public.uf_do_papel(current_user)
group by v.id, v.co_entidade, e.no_entidade, m.no_municipio, m.sg_uf,
         v.tecnico_id, v.origem, v.ocorrido_em, v.registrado_em, v.observacao
"""

# Recorte de linha na fila: a vistoria só aparece se a escola estiver na UF do
# técnico. As demais operações (carga/dbt/cdc) ganham políticas permissivas
# próprias, para que o RLS não quebre o pipeline.
RLS = [
    "alter table public.vistoria enable row level security",
    "alter table public.vistoria_item enable row level security",
    """
    create policy vistoria_carga on public.vistoria
        for all to eduinfra_carga, eduinfra_orquestrador
        using (true) with check (true)
    """,
    """
    create policy vistoria_leitura on public.vistoria
        for select to eduinfra_dbt, eduinfra_cdc
        using (true)
    """,
    """
    create policy vistoria_bancada on public.vistoria
        for all to eduinfra_cdc_bancada
        using (true) with check (true)
    """,
    """
    create policy vistoria_fila on public.vistoria
        for select to eduinfra_tecnico_df, eduinfra_tecnico_sp
        using (public.uf_da_escola(vistoria.co_entidade) = public.uf_do_papel(current_user))
    """,
    """
    create policy vistoria_item_carga on public.vistoria_item
        for all to eduinfra_carga, eduinfra_orquestrador
        using (true) with check (true)
    """,
    """
    create policy vistoria_item_leitura on public.vistoria_item
        for select to eduinfra_dbt, eduinfra_cdc
        using (true)
    """,
    """
    create policy vistoria_item_bancada on public.vistoria_item
        for all to eduinfra_cdc_bancada
        using (true) with check (true)
    """,
    """
    create policy vistoria_item_fila on public.vistoria_item
        for select to eduinfra_tecnico_df, eduinfra_tecnico_sp
        using (
            exists (
                select 1 from public.vistoria v
                where v.id = vistoria_item.vistoria_id
            )
        )
    """,
]

REMOVER_RLS = [
    "drop policy if exists vistoria_item_fila on public.vistoria_item",
    "drop policy if exists vistoria_item_bancada on public.vistoria_item",
    "drop policy if exists vistoria_item_leitura on public.vistoria_item",
    "drop policy if exists vistoria_item_carga on public.vistoria_item",
    "drop policy if exists vistoria_fila on public.vistoria",
    "drop policy if exists vistoria_bancada on public.vistoria",
    "drop policy if exists vistoria_leitura on public.vistoria",
    "drop policy if exists vistoria_carga on public.vistoria",
    "alter table public.vistoria_item disable row level security",
    "alter table public.vistoria disable row level security",
]


def _conexao_psycopg():
    return op.get_bind().connection.dbapi_connection


def _senha() -> str:
    return os.environ.get("EDUINFRA_SENHA_PAPEIS", SENHA_PADRAO)


def _criar_papeis() -> None:
    conexao = _conexao_psycopg()
    senha = sql.Literal(_senha())
    for papel in PAPEIS:
        # Idempotente: a migração pode ser reaplicada num ambiente já preparado.
        conexao.execute(
            sql.SQL(
                "do $$ begin "
                "if not exists (select 1 from pg_roles where rolname = {}) then "
                "create role {} nologin; "
                "end if; end $$"
            ).format(sql.Literal(papel), sql.Identifier(papel))
        )
        # O corpo do DO acima é texto: o identificador já sai renderizado.
        conexao.execute(
            sql.SQL("alter role {} with login password {}").format(sql.Identifier(papel), senha)
        )
    # O consumidor do slot lógico precisa do atributo de replicação.
    conexao.execute("alter role eduinfra_cdc with replication")
    # As ferramentas de bancada do CDC (demonstrar-cdc, comparar-ingestao,
    # medir-recarga, auditar-cdc) rodam na imagem de carga e criam/leem slots.
    # A ingestão de rotina não usa replicação; o atributo fica só para elas.
    conexao.execute("alter role eduinfra_cdc_bancada with replication")
    # Garante que uma base criada por uma versão anterior da 0010 perca o
    # atributo: a carga de rotina não deve replicar.
    conexao.execute("alter role eduinfra_carga with noreplication")
    # O orquestrador executa as duas metades do pipeline com os mesmos privilégios.
    conexao.execute("grant eduinfra_carga, eduinfra_dbt to eduinfra_orquestrador")


def _preparar_esquemas() -> None:
    for esquema, dono in ESQUEMAS.items():
        op.execute(f"create schema if not exists {esquema} authorization {dono}")
        # Em bases já existentes o esquema pode ter outro dono; normaliza.
        op.execute(f"alter schema {esquema} owner to {dono}")


def _normalizar_donos_existentes() -> None:
    """Objetos criados antes desta migração passam ao dono do esquema.

    Numa base nova os esquemas estão vazios e o bloco não faz nada. Numa base
    de desenvolvimento já construída, garante que o dbt/carga possam substituir
    as tabelas que constroem.
    """
    op.execute(
        """
        do $$
        declare r record; dono text;
        begin
            for r in
                select n.nspname, c.relname, c.relkind
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                where n.nspname in ('gold', 'fonte', 'entrada_transformacao')
                  and c.relkind in ('r', 'p', 'v', 'm')
            loop
                if r.nspname = 'gold' then
                    dono := 'eduinfra_dbt';
                else
                    dono := 'eduinfra_orquestrador';
                end if;
                if r.relkind in ('r', 'p') then
                    execute format('alter table %I.%I owner to %I', r.nspname, r.relname, dono);
                elsif r.relkind = 'v' then
                    execute format('alter view %I.%I owner to %I', r.nspname, r.relname, dono);
                elsif r.relkind = 'm' then
                    execute format('alter materialized view %I.%I owner to %I', r.nspname, r.relname, dono);
                end if;
            end loop;

            -- Sequences de identidade acompanham o dono da tabela; alterá-las
            -- separadamente é recusado pelo PostgreSQL.
            for r in
                select n.nspname, c.relname
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                where n.nspname in ('monitoramento', 'comparacao') and c.relkind in ('r', 'p')
            loop
                execute format('alter table %I.%I owner to eduinfra_carga', r.nspname, r.relname);
            end loop;
        end
        $$
        """
    )


def _conceder_esquemas() -> None:
    # Gold: o dbt constrói; carga executa a opção nula; painel/análise só leem.
    op.execute("grant usage on schema gold to eduinfra_carga, eduinfra_painel, eduinfra_analise, eduinfra_orquestrador")
    op.execute("grant usage, create on schema gold to eduinfra_dbt")
    op.execute("grant select on all tables in schema gold to eduinfra_carga, eduinfra_painel, eduinfra_analise")
    op.execute("grant select on all sequences in schema gold to eduinfra_carga")

    # Entrada congelada: só o orquestrador escreve; o dbt lê para o build.
    op.execute("grant usage on schema entrada_transformacao to eduinfra_dbt, eduinfra_orquestrador")
    op.execute("grant create on schema entrada_transformacao to eduinfra_orquestrador")
    op.execute("grant select on all tables in schema entrada_transformacao to eduinfra_dbt, eduinfra_orquestrador")

    # Fonte do CDC: o orquestrador escreve; o dbt só lê (opção nula construir-gold).
    op.execute("grant usage on schema fonte to eduinfra_dbt, eduinfra_orquestrador")
    op.execute("grant create on schema fonte to eduinfra_orquestrador")
    op.execute("grant select on all tables in schema fonte to eduinfra_dbt, eduinfra_orquestrador")

    # Monitoramento: a carga registra medições; o orquestrador lê e registra o
    # frescor do pipeline. Painel e análise não alcançam a camada operacional.
    op.execute("grant usage on schema monitoramento to eduinfra_carga, eduinfra_orquestrador")
    op.execute("grant select, insert on monitoramento.frescor_gold to eduinfra_carga, eduinfra_orquestrador")
    op.execute("grant usage on all sequences in schema monitoramento to eduinfra_orquestrador")

    # Comparação é uma ferramenta de bancada, do papel de carga.
    op.execute("grant usage, create on schema comparacao to eduinfra_carga")

    # Camada de métricas é opcional (entrega da #98). Se existir, o painel só lê.
    op.execute(
        """
        do $$
        begin
            if exists (select 1 from pg_namespace where nspname = 'metricas') then
                execute 'grant usage on schema metricas to eduinfra_painel, eduinfra_analise';
                execute 'grant select on all tables in schema metricas to eduinfra_painel, eduinfra_analise';
            end if;
        end
        $$
        """
    )

    # Privilégios padrão: objetos criados depois da migração (pelo dbt) já nascem
    # legíveis para o painel/análise, sem um GRANT manual a cada construção.
    op.execute(
        "alter default privileges for role eduinfra_dbt in schema gold "
        "grant select on tables to eduinfra_carga, eduinfra_painel, eduinfra_analise"
    )
    op.execute(
        "alter default privileges for role eduinfra_orquestrador in schema entrada_transformacao "
        "grant select on tables to eduinfra_dbt"
    )
    op.execute(
        "alter default privileges for role eduinfra_orquestrador in schema fonte "
        "grant select on tables to eduinfra_dbt"
    )
    op.execute(
        "alter default privileges for role eduinfra_carga in schema monitoramento "
        "grant select, insert on tables to eduinfra_orquestrador"
    )


def _criar_fila_por_uf() -> None:
    op.execute(CRIAR_MAPA_UF)
    op.execute("revoke all on public.acesso_uf_papel from public")
    op.execute(CRIAR_FUNCAO_UF)
    op.execute("revoke all on function public.uf_do_papel(name) from public")
    op.execute(
        "grant execute on function public.uf_do_papel(name) to eduinfra_tecnico_df, eduinfra_tecnico_sp"
    )
    op.execute(CRIAR_FUNCAO_UF_DA_ESCOLA)
    op.execute("revoke all on function public.uf_da_escola(integer) from public")
    op.execute(
        "grant execute on function public.uf_da_escola(integer) to eduinfra_tecnico_df, eduinfra_tecnico_sp"
    )
    for papel, uf in FILA_POR_UF.items():
        conexao = _conexao_psycopg()
        conexao.execute(
            sql.SQL(
                "insert into public.acesso_uf_papel (papel, uf) values ({}, {}) "
                "on conflict (papel) do update set uf = excluded.uf"
            ).format(sql.Literal(papel), sql.Literal(uf))
        )
    # A visão é dona da carga (que lê escola/município); o técnico só a consulta.
    op.execute(CRIAR_VISAO_FILA)
    op.execute("alter view public.fila_vistoria owner to eduinfra_carga")
    op.execute("revoke all on public.fila_vistoria from public")
    op.execute(
        "grant select on public.fila_vistoria to eduinfra_tecnico_df, eduinfra_tecnico_sp"
    )


def upgrade() -> None:
    _criar_papeis()
    _preparar_esquemas()
    op.execute(CRIAR_FRESCOR)
    _normalizar_donos_existentes()
    _conceder_esquemas()

    for comando in GRANTS:
        op.execute(comando)

    colunas = ", ".join(COLUNAS_TECNICO)
    # Só o recorte por coluna é concedido a eduinfra_analise; não há GRANT de
    # tabela inteira, então nome e matrícula permanecem fora do alcance.
    op.execute(
        f"grant select ({colunas}) on public.tecnico to eduinfra_analise"
    )

    _criar_fila_por_uf()

    for comando in RLS:
        op.execute(comando)


def downgrade() -> None:
    for comando in REMOVER_RLS:
        op.execute(comando)

    op.execute("drop view if exists public.fila_vistoria")
    op.execute("drop function if exists public.uf_da_escola(integer)")
    op.execute("drop function if exists public.uf_do_papel(name)")
    op.execute("drop table if exists public.acesso_uf_papel")

    # Devolve os objetos ao superusuário que roda a migração antes de apagar os papéis.
    conexao = _conexao_psycopg()
    dono = conexao.execute("select current_user").fetchone()[0]
    for papel in reversed(PAPEIS):
        conexao.execute(
            sql.SQL("reassign owned by {} to {}").format(
                sql.Identifier(papel), sql.Identifier(dono)
            )
        )
        conexao.execute(sql.SQL("drop owned by {}").format(sql.Identifier(papel)))
        conexao.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(papel)))
