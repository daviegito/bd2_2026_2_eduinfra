create or replace function metricas.validar_filtros(f jsonb, permitidos text[], exige_item boolean)
returns table (ano integer, area text, uf text, municipio integer, rede text,
               localizacao text, item text, situacao text)
language plpgsql stable as $filtros$
declare chave text;
begin
    if jsonb_typeof(f) is distinct from 'object' then
        raise exception 'Filtros devem ser um objeto JSON';
    end if;
    for chave in select jsonb_object_keys(f) loop
        if not chave = any(permitidos) then
            raise exception 'Filtro não aceito por esta métrica: %', chave;
        end if;
    end loop;
    if jsonb_typeof(f->'ano') is distinct from 'number'
       or coalesce(f->>'ano', '') !~ '^[0-9]{1,4}$'
       or (f->>'ano')::integer < 1 then
        raise exception 'Informe ano como inteiro entre 1 e 9999';
    end if;
    ano := (f->>'ano')::integer;
    area := coalesce(f->>'area', 'media_geral');
    if area not in ('media_geral','media_cn','media_ch','media_lc','media_mt','media_redacao') then
        raise exception 'Área da prova inválida';
    end if;
    uf := f->>'uf';
    if uf is not null and uf not in ('AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS',
        'MG','PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO') then
        raise exception 'UF inválida';
    end if;
    if f ? 'municipio' then
        if jsonb_typeof(f->'municipio') is distinct from 'number'
           or coalesce(f->>'municipio','') !~ '^[0-9]{1,7}$' or (f->>'municipio')::integer < 1 then
            raise exception 'Município deve ser um código inteiro positivo';
        end if;
        municipio := (f->>'municipio')::integer;
        if not exists (select 1 from gold.dim_municipio m where m.co_municipio = municipio) then
            raise exception 'Município não encontrado no catálogo';
        end if;
    end if;
    rede := f->>'rede';
    if rede is not null and rede not in ('federal','estadual','municipal','privada') then
        raise exception 'Rede inválida';
    end if;
    localizacao := f->>'localizacao';
    if localizacao is not null and localizacao not in ('urbana','rural') then
        raise exception 'Localização inválida';
    end if;
    item := f->>'item';
    if exige_item and item is null then raise exception 'Informe o item'; end if;
    if item is not null and not exists (select 1 from gold.dim_item i where i.item_codigo = item) then
        raise exception 'Item não encontrado no catálogo';
    end if;
    situacao := f->>'situacao';
    if situacao is not null and (item is null or situacao not in
        ('existente','inexistente','em_obra','inutilizavel','nao_informado')) then
        raise exception 'Situação inválida ou informada sem item';
    end if;
    if 'situacao' = any(permitidos) and item is not null and situacao is null then
        raise exception 'Informe situação ao filtrar por item';
    end if;
    return next;
end
$filtros$;
