-- Integridade referencial: o SQLMesh não traz este audit pronto.
AUDIT (name referencia_existe);

select *
from @this_model as filho
where filho.@coluna is not null
  and not exists (select 1 from @destino as pai where pai.@campo = filho.@coluna)
