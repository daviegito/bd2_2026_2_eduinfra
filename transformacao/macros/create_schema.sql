{# O papel do dbt não tem CREATE no banco (privilégio mínimo, issue #86). Os
   esquemas são criados pela migração 0010; aqui só conferimos que existem,
   em vez de executar "create schema if not exists", que exigiria CREATE no banco. #}
{% macro postgres__create_schema(relation) -%}
  {% if relation.database -%}
    {{ adapter.verify_database(relation.database) }}
  {%- endif -%}
  {%- set existe = run_query("select 1 from pg_namespace where nspname = " ~ adapter.quote(relation.schema)) -%}
  {%- if existe.rows | length == 0 -%}
    {{ exceptions.raise_compiler_error(
        "esquema " ~ relation.schema ~ " ausente; rode `docker compose run --rm carga migrar` (migração 0010)"
    ) }}
  {%- endif -%}
{% endmacro %}
