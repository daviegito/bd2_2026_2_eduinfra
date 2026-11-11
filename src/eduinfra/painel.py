"""Painel interativo, exportável e sem servidor, a partir de um retrato da Gold."""

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from plotly.offline import get_plotlyjs

from eduinfra.configuracao import carregar_configuracao

MINIMO_ESCOLAS = 10
METRICAS = (
    ("media_geral", "Média geral"),
    ("media_cn", "Ciências da Natureza"),
    ("media_ch", "Ciências Humanas"),
    ("media_lc", "Linguagens"),
    ("media_mt", "Matemática"),
    ("media_redacao", "Redação"),
)
ESTADOS = {"existente": "1", "inexistente": "2", "em_obra": "3", "inutilizavel": "4"}


def conectar(configuracao):
    conexao = duckdb.connect()
    try:
        conexao.execute("install postgres; load postgres")
        dsn = configuracao.dsn.replace("'", "''")
        conexao.execute(f"attach '{dsn}' as pg (type postgres, read_only)")
        conexao.execute("begin transaction")
        return conexao
    except Exception:
        conexao.close()
        raise


def preparar_base(conexao, ano):
    exigidas = {
        ("gold", "dim_escola"), ("gold", "dim_municipio"), ("gold", "dim_item"),
        ("gold", "dim_escola_infraestrutura"), ("gold", "fato_desempenho_escola_ano"),
        ("entrada_transformacao", "enem_escola_ano"),
    }
    presentes = set(conexao.execute("""select table_schema, table_name
        from information_schema.tables where table_catalog = 'pg'""").fetchall())
    if exigidas - presentes:
        raise RuntimeError("Gold completa ausente: execute docker compose run --build --rm transformacao")
    conexao.execute("""create temp table base_enem as
        select f.*, d.escola_infra_sk as perfil, d.itens
        from pg.gold.fato_desempenho_escola_ano f
        join pg.gold.dim_escola e using (co_entidade)
        left join pg.gold.dim_escola_infraestrutura d
          on d.escola_infra_sk = f.escola_infra_sk and d.co_entidade = f.co_entidade
        where f.ano = ? and e.ano_censo_referencia = ?""", [ano, ano])
    quantidade, unicas, inicio, fim = conexao.execute("""select count(*),
        count(distinct co_entidade), min(data_referencia_infraestrutura),
        max(data_referencia_infraestrutura) from base_enem""").fetchone()
    if not quantidade or quantidade != unicas:
        raise RuntimeError(f"Gold ENEM {ano} vazia ou com mais de uma linha por escola; reexecute transformacao")
    if inicio is None or inicio != fim:
        raise RuntimeError("A Gold precisa ter uma única referência de infraestrutura para o ano do painel")
    conexao.execute("""create temp table base_censo as
        select e.co_entidade, e.tp_dependencia, e.localizacao, e.co_municipio,
               m.no_municipio, m.sg_uf, d.escola_infra_sk as perfil, d.itens
        from pg.gold.dim_escola e
        left join pg.gold.dim_municipio m using (co_municipio)
        left join pg.gold.dim_escola_infraestrutura d
          on d.co_entidade = e.co_entidade
         and d.valid_from <= cast(? as timestamp) at time zone 'UTC'
         and cast(? as timestamp) at time zone 'UTC' < d.valid_to
        where e.ano_censo_referencia = ?""", [inicio, inicio, ano])
    total, distintas = conexao.execute("select count(*), count(distinct co_entidade) from base_censo").fetchone()
    if not total or total != distintas:
        raise RuntimeError("Censo vazio ou perfis temporais sobrepostos; reexecute e valide a Gold")
    return inicio


def ler_cobertura(conexao, ano):
    total, distintas, encontradas, mascaradas = conexao.execute("""select count(*),
        count(distinct e.co_entidade), count(c.co_entidade),
        count(*) filter (where c.co_entidade is null and e.poucos_participantes)
        from pg.entrada_transformacao.enem_escola_ano e
        left join base_censo c using (co_entidade) where e.ano = ?""", [ano]).fetchone()
    diferentes = conexao.execute("""select count(*) from (
        (select e.co_entidade from pg.entrada_transformacao.enem_escola_ano e
         join base_censo c using (co_entidade) where e.ano = ?
         except select co_entidade from base_enem)
        union all
        (select co_entidade from base_enem except
         select e.co_entidade from pg.entrada_transformacao.enem_escola_ano e
         join base_censo c using (co_entidade) where e.ano = ?))""", [ano, ano]).fetchone()[0]
    if not total or total != distintas or diferentes:
        raise RuntimeError("Gold ENEM difere da cobertura de entrada; reexecute transformacao antes do painel")
    return {"entrada": total, "identificadas": encontradas,
            "sem_juncao": total - encontradas, "mascaradas": mascaradas}


def codificar_itens(itens, catalogo):
    perfil = json.loads(itens) if isinstance(itens, str) else itens or {}
    return "".join(ESTADOS.get(perfil.get(codigo), "0") for codigo, _ in catalogo)


def nota_valida(valor):
    if valor is None:
        return None
    numero = float(valor)
    return numero if math.isfinite(numero) else None


def ler_dados(conexao, ano):
    referencia = preparar_base(conexao, ano)
    cobertura = ler_cobertura(conexao, ano)
    catalogo = conexao.execute("select item_codigo, descricao from pg.gold.dim_item order by item_codigo").fetchall()
    if not catalogo or len({codigo for codigo, _ in catalogo}) != len(catalogo):
        raise RuntimeError("Catálogo Gold vazio ou duplicado; reexecute transformacao")
    municipios = conexao.execute("""select distinct co_municipio,
        coalesce(no_municipio, 'Município não informado'), coalesce(sg_uf, 'Não informada')
        from base_censo order by co_municipio""").fetchall()
    indice_municipios = {linha[0]: indice for indice, linha in enumerate(municipios)}
    escolas = []
    indice_escolas = {}
    for codigo, rede, local, municipio, perfil, itens in conexao.execute("""select
        co_entidade, tp_dependencia, localizacao, co_municipio, perfil, itens
        from base_censo order by co_entidade""").fetchall():
        indice_escolas[codigo] = len(escolas)
        escolas.append([indice_municipios[municipio], rede,
                        {"urbana": 1, "rural": 2}.get(local, 0),
                        codificar_itens(itens, catalogo), perfil is not None])
    colunas = ", ".join(nome for nome, _ in METRICAS)
    enem = []
    for codigo, perfil, itens, participantes, *notas in conexao.execute(f"""select
        co_entidade, perfil, itens, n_participantes, {colunas}
        from base_enem order by co_entidade""").fetchall():
        enem.append([indice_escolas[codigo], codificar_itens(itens, catalogo),
                     [nota_valida(nota) for nota in notas], participantes, perfil is not None])
    return {"ano": ano, "referencia": str(referencia), "minimo": MINIMO_ESCOLAS,
            "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "itens": catalogo, "metricas": METRICAS, "municipios": municipios,
            "escolas": escolas, "enem": enem, "cobertura": cobertura}


CALCULOS = r"""
'use strict';
const EduInfra = (() => {
    const redes = {1: 'Federal', 2: 'Estadual', 3: 'Municipal', 4: 'Privada'};
    const estados = ['Não informado', 'Possui', 'Não possui', 'Em obra', 'Inutilizável'];
    const numeros = valores => valores.filter(x => typeof x === 'number' && Number.isFinite(x));
    function resumo(valores, minimo) {
        const notas = numeros(valores);
        const n = notas.length;
        if (n < minimo) return {n, media: null, desvio: null};
        const media = notas.reduce((total, nota) => total + nota, 0) / n;
        const variancia = n > 1 ? notas.reduce((total, nota) => total + (nota - media) ** 2, 0) / (n - 1) : 0;
        return {n, media, desvio: Math.sqrt(variancia)};
    }
    function corresponde(escola, municipio, filtro) {
        return (!filtro.uf || municipio[2] === filtro.uf)
            && (filtro.municipio === '' || filtro.municipio === undefined || escola[0] === Number(filtro.municipio))
            && (!filtro.rede || (filtro.rede === 'publica' ? [1, 2, 3].includes(escola[1]) : escola[1] === Number(filtro.rede)))
            && (!filtro.local || escola[2] === Number(filtro.local));
    }
    function calcular(dados, filtro = {}) {
        const item = Number(filtro.item || 0);
        const metrica = Number(filtro.metrica || 0);
        const escolhidas = new Set();
        const infraestrutura = dados.itens.map(() => [0, 0, 0, 0, 0]);
        const porUf = new Map();
        const redesCenso = new Map();
        let perfisCenso = 0;
        dados.escolas.forEach((escola, indice) => {
            const municipio = dados.municipios[escola[0]];
            if (!corresponde(escola, municipio, filtro)) return;
            escolhidas.add(indice);
            perfisCenso += Number(escola[4]);
            const rede = redes[escola[1]] || 'Não informada';
            redesCenso.set(rede, (redesCenso.get(rede) || 0) + 1);
            if (!porUf.has(municipio[2])) porUf.set(municipio[2], {total: 0, estados: [0, 0, 0, 0, 0]});
            const uf = porUf.get(municipio[2]);
            uf.total++;
            uf.estados[Number(escola[3][item] || 0)]++;
            infraestrutura.forEach((contagem, posicao) => contagem[Number(escola[3][posicao] || 0)]++);
        });
        const registros = dados.enem.filter(registro => escolhidas.has(registro[0]));
        const grupos = new Map();
        const desempenho = new Map();
        let participantes = 0;
        let perfisEnem = 0;
        registros.forEach(registro => {
            const escola = dados.escolas[registro[0]];
            const rede = redes[escola[1]] || 'Não informada';
            const local = {1: 'Urbana', 2: 'Rural'}[escola[2]] || 'Não informada';
            const estado = Number(registro[1][item] || 0);
            const chave = `${rede}|${estado}`;
            if (!grupos.has(chave)) grupos.set(chave, {rede, estado, notas: []});
            grupos.get(chave).notas.push(registro[2][metrica]);
            const recorte = `${rede} · ${local}`;
            if (!desempenho.has(recorte)) desempenho.set(recorte, []);
            desempenho.get(recorte).push(registro[2][metrica]);
            participantes += registro[3];
            perfisEnem += Number(registro[4]);
        });
        const comparacoes = Array.from(grupos.values()).map(grupo => ({
            rede: grupo.rede, estado: grupo.estado, ...resumo(grupo.notas, dados.minimo)
        })).sort((a, b) => a.rede.localeCompare(b.rede) || a.estado - b.estado);
        const pares = [];
        for (const rede of Object.values(redes)) {
            const com = comparacoes.find(g => g.rede === rede && g.estado === 1 && g.media !== null);
            const sem = comparacoes.find(g => g.rede === rede && g.estado === 2 && g.media !== null);
            if (com && sem) pares.push({rede, com, sem, diferenca: com.media - sem.media});
        }
        const porArea = dados.metricas.map((metrica, indice) => ({
            nome: metrica[1], ...resumo(registros.map(r => r[2][indice]), dados.minimo)
        }));
        return {
            censo: escolhidas.size, enem: registros.length, participantes, perfisCenso, perfisEnem,
            escolasSemNota: registros.filter(r => !numeros([r[2][metrica]]).length).length,
            notas: numeros(registros.map(r => r[2][metrica])),
            nota: resumo(registros.map(r => r[2][metrica]), dados.minimo),
            infraestrutura, porUf: Array.from(porUf, ([uf, contagem]) => ({uf, ...contagem})),
            redesCenso: Array.from(redesCenso, ([rede, n]) => ({rede, n})),
            desempenho: Array.from(desempenho, ([grupo, notas]) => ({grupo, ...resumo(notas, dados.minimo)})),
            comparacoes, pares, porArea,
            gruposPequenos: comparacoes.filter(g => g.media === null).length
        };
    }
    return {calcular, resumo, corresponde, redes, estados};
})();
if (typeof module !== 'undefined') module.exports = EduInfra;
"""

ESTILOS = """
:root{--fundo:#f5f6f1;--papel:#fff;--tinta:#20392f;--verde:#1c6650;--muted:#63756a;--linha:#dce3d9;--laranja:#c47c39}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:185px}
body{margin:0;background:var(--fundo);color:var(--tinta);font:16px/1.55 'DM Sans',system-ui,sans-serif}
a{color:inherit}button,select{font:inherit}button{cursor:pointer}button:focus-visible,a:focus-visible,select:focus-visible{outline:3px solid #c47c39;outline-offset:3px}
.shell{max-width:1500px;margin:auto;padding:0 42px 64px}.topo{display:flex;justify-content:space-between;align-items:center;padding:25px 0;border-bottom:1px solid var(--linha);gap:20px}
.marca{font-size:20px;letter-spacing:-.7px;font-weight:800}.marca span{color:var(--verde)}.edicao{font-size:13px;color:var(--muted)}
.hero{display:grid;grid-template-columns:1.6fr 1fr;gap:50px;padding:45px 0 32px;align-items:end}.hero h1{font-size:clamp(30px,3.3vw,49px);line-height:1.13;letter-spacing:-1.6px;max-width:800px;margin:10px 0 18px;font-weight:650}.eyebrow{text-transform:uppercase;letter-spacing:2px;color:var(--verde);font-size:12px;font-weight:750}.hero p{color:var(--muted);margin:0;max-width:730px}.hero aside{border-left:3px solid var(--verde);padding:4px 0 4px 22px;font-size:14px;color:var(--muted)}
.filtros{position:sticky;top:0;z-index:5;background:#f5f6f1f5;backdrop-filter:blur(12px);border:1px solid var(--linha);border-radius:12px;padding:17px 18px;box-shadow:0 5px 16px #20392f08}.campos{display:grid;grid-template-columns:1fr 1.45fr 1fr 1fr 1.4fr 1.4fr;gap:12px}.campo label{display:block;color:var(--muted);font-size:11px;font-weight:750;text-transform:uppercase;letter-spacing:.7px;margin-bottom:5px}select{width:100%;min-height:42px;padding:8px 24px 8px 10px;border:1px solid #ccd6c9;border-radius:6px;color:var(--tinta);background:white;font-size:13px}.filtro-rodape{display:flex;justify-content:space-between;gap:15px;align-items:center;margin-top:10px;font-size:12px;color:var(--muted)}.reset{border:0;background:none;color:var(--verde);padding:3px 0;text-decoration:underline;font-size:12px}.navegacao{display:flex;gap:24px;overflow:auto;padding:20px 0;font-size:13px;border-bottom:1px solid var(--linha)}.navegacao a{text-decoration:none;white-space:nowrap}.navegacao a:hover{color:var(--verde);text-decoration:underline}
section{padding-top:36px}h2{margin:3px 0 8px;font-size:27px;font-weight:650;letter-spacing:-.6px}h3{font-size:17px;margin:0 0 6px;line-height:1.35}.introducao{margin:0 0 22px;font-size:14px;color:var(--muted)}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.kpi{background:white;border:1px solid var(--linha);border-radius:10px;padding:22px}.kpi .rotulo{font-size:12px;color:var(--muted)}.kpi strong{font-size:34px;display:block;font-weight:650;letter-spacing:-1px;margin:5px 0 2px}.kpi small{display:block;color:var(--muted);font-size:11px}.kpi:first-child{background:var(--verde);color:white;border-color:var(--verde)}.kpi:first-child .rotulo,.kpi:first-child small{color:#d5e6dc}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px}.quadro{min-width:0;background:white;border:1px solid var(--linha);border-radius:10px;padding:24px}.quadro.largo{grid-column:1/-1}.legenda{color:var(--muted);font-size:12px;margin:0 0 10px}.grafico{min-height:300px;overflow:hidden}.perguntas{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin-top:22px}.resposta{padding:22px;border:1px solid var(--linha);border-radius:10px;background:#eaf0e7}.resposta p{font-size:14px;color:#40584b;margin:12px 0 0}.resposta small{font-size:11px;color:var(--muted)}.perguntas .resposta:first-child{background:#e1ecdf}
.vazio{padding:30px 10px;color:var(--muted);font-size:14px}.aviso{background:#fff2de;border:1px solid #ebd6b5;border-radius:8px;padding:14px 18px;font-size:13px;margin-top:20px;color:#6e512c}.metodo{margin-top:35px;background:#eaf0e7;padding:24px;border-radius:10px;font-size:13px;color:#40584b}.metodo summary{cursor:pointer;font-weight:700}.metodo p{margin:12px 0 0}.rodape{margin-top:28px;padding-top:20px;border-top:1px solid var(--linha);font-size:12px;color:var(--muted)}table{border-collapse:collapse;width:100%;font-size:13px;margin-top:16px}th,td{padding:10px 8px;text-align:left;border-bottom:1px solid var(--linha)}th{font-weight:650;color:var(--muted)}td:not(:first-child),th:not(:first-child){text-align:right}.tabela-container{overflow-x:auto}.visually-hidden{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:1100px){.campos{grid-template-columns:repeat(3,1fr)}.hero{grid-template-columns:1fr}.hero aside{max-width:800px}.perguntas{grid-template-columns:1fr 1fr}html{scroll-padding-top:230px}}
@media(max-width:700px){.shell{padding:0 16px 40px}.topo{padding:17px 0}.edicao{max-width:170px;text-align:right}.hero{padding:27px 0;gap:20px}.hero h1{font-size:32px}.hero aside{font-size:12px}.campos{grid-template-columns:1fr 1fr}.filtros{position:static;padding:13px}.grid,.kpis,.perguntas{grid-template-columns:1fr}.kpis{grid-template-columns:1fr 1fr;gap:10px}.kpi{padding:16px}.kpi strong{font-size:27px}.quadro{padding:15px}.navegacao{gap:18px}h2{font-size:24px}html{scroll-padding-top:15px}.filtro-rodape{align-items:start}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""

INTERFACE = r"""
'use strict';
const dados = JSON.parse(document.getElementById('dados-painel').textContent);
const numero = new Intl.NumberFormat('pt-BR');
const dataReferencia = dados.referencia.split('-').reverse().join('/');
const decimal = new Intl.NumberFormat('pt-BR', {minimumFractionDigits: 1, maximumFractionDigits: 1});
const cores = ['#9ca89b', '#2e795b', '#b65c43', '#d6a74c', '#8b7393'];
const porId = id => document.getElementById(id);
const porcentagem = (n, total) => total ? `${decimal.format(100 * n / total)}%` : '—';
function texto(id, valor) { porId(id).textContent = valor; }
function opcao(select, valor, rotulo) {
    const item = document.createElement('option'); item.value = valor; item.textContent = rotulo; select.add(item);
}
function obterFiltros() {
    return Object.fromEntries(['uf', 'municipio', 'rede', 'local', 'item', 'metrica'].map(id => [id, porId(id).value]));
}
function atualizarMunicipios() {
    const select = porId('municipio');
    select.replaceChildren(); opcao(select, '', 'Todos os municípios');
    dados.municipios.map((m, indice) => ({m, indice}))
        .filter(({m}) => !porId('uf').value || m[2] === porId('uf').value)
        .sort((a, b) => a.m[1].localeCompare(b.m[1], 'pt-BR'))
        .forEach(({m, indice}) => opcao(select, indice, `${m[1]} · ${m[2]}`));
}
function grafico(id, traces, extras = {}, vazio = 'Sem escolas suficientes para este gráfico no recorte.') {
    const elemento = porId(id);
    if (!traces.length) {
        if (elemento.classList.contains('js-plotly-plot')) Plotly.purge(elemento);
        elemento.replaceChildren();
        elemento.style.height = 'auto';
        const mensagem = document.createElement('p'); mensagem.className = 'vazio'; mensagem.textContent = vazio;
        elemento.appendChild(mensagem); return;
    }
    elemento.querySelectorAll('.vazio').forEach(mensagem => mensagem.remove());
    const layout = {
        paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
        font: {family: 'DM Sans, system-ui, sans-serif', color: '#40584b', size: 12},
        margin: {l: 65, r: 24, t: 18, b: 70}, height: 350, separators: ',.',
        xaxis: {gridcolor: '#edf0ea', zeroline: false, automargin: true},
        yaxis: {gridcolor: '#edf0ea', zeroline: false, automargin: true},
        legend: {orientation: 'h', y: -0.3}, ...extras
    };
    elemento.style.height = `${layout.height}px`;
    Plotly.react(elemento, traces, layout, {responsive: true, displaylogo: false,
        modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'],
        toImageButtonOptions: {format: 'png', filename: `eduinfra-${id}-${dados.ano}`}});
}
function montarTabela(id, cabecalhos, linhas) {
    const container = porId(id); container.replaceChildren();
    const tabela = document.createElement('table');
    const thead = tabela.createTHead().insertRow();
    cabecalhos.forEach(valor => {const th = document.createElement('th'); th.scope = 'col'; th.textContent = valor; thead.appendChild(th);});
    const tbody = tabela.createTBody();
    linhas.forEach(linha => {const tr = tbody.insertRow(); linha.forEach(valor => {tr.insertCell().textContent = valor;});});
    container.appendChild(tabela);
}
function atualizarPerguntas(resultado, filtro) {
    const total = resultado.censo;
    const ordenados = dados.itens.map((item, i) => ({nome: item[1], n: resultado.infraestrutura[i][2]}))
        .filter(item => item.n > 0).sort((a, b) => b.n - a.n || a.nome.localeCompare(b.nome)).slice(0, 3);
    texto('resposta-base', total ? `${numero.format(total)} escolas do Censo compõem este recorte. ${numero.format(resultado.enem)} têm ENEM identificado (${porcentagem(resultado.enem, total)}).` : 'Este recorte não contém escolas do Censo. Amplie os filtros.');
    texto('resposta-deficits', !total ? 'Não há escolas neste recorte.' : ordenados.length
        ? ordenados.map(item => `${item.nome}: ${porcentagem(item.n, total)} (${numero.format(item.n)} escolas)`).join('; ') + '. Percentuais de ausência declarada sobre todas as escolas do recorte.'
        : 'Nenhum item tem ausência declarada neste recorte. Confira as informações desconhecidas antes de interpretar o resultado.');
    texto('resposta-notas', resultado.nota.media === null
        ? `Há ${resultado.nota.n} escolas com nota válida. São necessárias pelo menos ${dados.minimo} para exibir médias.`
        : `${dados.metricas[Number(filtro.metrica)][1]} de ${decimal.format(resultado.nota.media)} pontos, com ${numero.format(resultado.nota.n)} escolas. Desvio padrão entre as médias escolares: ${decimal.format(resultado.nota.desvio)} pontos.`);
    const par = resultado.pares.find(p => p.rede === 'Estadual') || resultado.pares[0];
    texto('resposta-relacao', par
        ? `Na rede ${par.rede.toLowerCase()}, as escolas com ${dados.itens[Number(filtro.item)][1].toLowerCase()} têm média de ${decimal.format(par.com.media)} (${numero.format(par.com.n)} escolas). Sem o item: ${decimal.format(par.sem.media)} (${numero.format(par.sem.n)} escolas). Diferença observada: ${decimal.format(par.diferenca)} pontos. Associação sem controle socioeconômico.`
        : `Não há grupos com pelo menos ${dados.minimo} escolas com nota válida em ambas as situações, dentro da mesma rede. Amplie o recorte ou selecione outro item.`);
    texto('resposta-informacao', total
        ? `${porcentagem(resultado.infraestrutura[Number(filtro.item)][0], total)} das escolas não têm informação conhecida sobre o item selecionado. ${numero.format(total - resultado.perfisCenso)} escolas estão sem perfil na data de referência.`
        : 'Não há escolas para avaliar a informação neste recorte.');
    texto('resposta-cobertura', `Na base nacional, ${numero.format(dados.cobertura.sem_juncao)} códigos do ENEM não encontraram escola no Censo. Desses, ${numero.format(dados.cobertura.mascaradas)} estão marcados como tendo menos de dez participantes. Essa contagem nacional não muda com os filtros.`);
}
function atualizar() {
    const filtro = obterFiltros();
    const r = EduInfra.calcular(dados, filtro);
    const item = Number(filtro.item);
    texto('n-censo', numero.format(r.censo)); texto('n-enem', numero.format(r.enem));
    texto('n-nota', r.nota.media === null ? '—' : decimal.format(r.nota.media));
    texto('n-nota-base', `${numero.format(r.nota.n)} escolas com nota válida · ${dados.metricas[Number(filtro.metrica)][1]}`);
    texto('n-perfil', porcentagem(r.perfisCenso, r.censo));
    texto('n-perfil-base', `${numero.format(r.perfisCenso)} perfis na referência de ${dataReferencia}`);
    texto('estado-filtros', `${numero.format(r.censo)} escolas no recorte · ${numero.format(r.enem)} na junção com o ENEM`);
    texto('titulo-item', `${dados.itens[item][1]} por UF`);
    texto('titulo-relacao', `Como as notas variam com ${dados.itens[item][1].toLowerCase()}?`);
    texto('base-infra', `Censo ${dados.ano} · ${numero.format(r.censo)} escolas no recorte · perfil em ${dataReferencia}`);
    texto('base-notas', `ENEM ${dados.ano} identificado no Censo · ${numero.format(r.enem)} escolas no recorte`);
    atualizarPerguntas(r, filtro);
    grafico('grafico-redes', r.censo ? [{type: 'bar', x: r.redesCenso.map(g => g.rede), y: r.redesCenso.map(g => g.n),
        marker: {color: '#2e795b'}, text: r.redesCenso.map(g => numero.format(g.n)), textposition: 'outside',
        hovertemplate: '%{x}<br>%{y} escolas<extra></extra>'}] : [], {yaxis: {title: {text: 'Escolas do Censo'}, automargin: true}});
    const ordem = dados.itens.map((_, i) => i).sort((a, b) => r.infraestrutura[a][1] - r.infraestrutura[b][1]);
    grafico('grafico-itens', r.censo ? [1, 2, 3, 4, 0].map(estado => ({type: 'bar', orientation: 'h',
        name: EduInfra.estados[estado], y: ordem.map(i => dados.itens[i][1]),
        x: ordem.map(i => 100 * r.infraestrutura[i][estado] / r.censo),
        customdata: ordem.map(i => r.infraestrutura[i][estado]), marker: {color: cores[estado]},
        hovertemplate: '%{y}<br>%{fullData.name}: %{x:.1f}%<br>%{customdata} escolas<extra></extra>'})) : [],
        {barmode: 'stack', height: Math.max(370, dados.itens.length * 30 + 100), margin: {l: 175, r: 20, t: 15, b: 100},
         xaxis: {range: [0, 100], ticksuffix: '%', title: {text: '% de todas as escolas do recorte'}}});
    const ufs = r.porUf.sort((a, b) => b.estados[1] / b.total - a.estados[1] / a.total || a.uf.localeCompare(b.uf));
    grafico('grafico-uf', ufs.length ? [{type: 'bar', x: ufs.map(g => g.uf), y: ufs.map(g => 100 * g.estados[1] / g.total),
        customdata: ufs.map(g => [g.estados[1], g.total, g.estados[0]]), marker: {color: '#2e795b'},
        hovertemplate: '%{x}<br>Possui: %{y:.1f}%<br>%{customdata[0]} de %{customdata[1]} escolas<br>Não informado: %{customdata[2]}<extra></extra>'}] : [],
        {yaxis: {range: [0, 105], ticksuffix: '%', automargin: true}, xaxis: {automargin: true}});
    montarTabela('tabela-infra', ['Situação do item selecionado', 'Escolas', '% do recorte'],
        [1, 2, 3, 4, 0].map(estado => [EduInfra.estados[estado], numero.format(r.infraestrutura[item][estado]), porcentagem(r.infraestrutura[item][estado], r.censo)]));
    const areas = r.porArea.filter(g => g.media !== null);
    grafico('grafico-areas', areas.length ? [{type: 'bar', x: areas.map(g => g.nome), y: areas.map(g => g.media),
        customdata: areas.map(g => g.n), marker: {color: '#2e795b'},
        hovertemplate: '%{x}<br>Média das escolas: %{y:.1f}<br>n = %{customdata} escolas<extra></extra>'}] : [],
        {yaxis: {title: {text: 'Média das médias escolares'}, automargin: true}});
    grafico('grafico-distribuicao', r.nota.media !== null ? [{type: 'histogram', x: r.notas, nbinsx: 24,
        marker: {color: '#81a58a', line: {color: '#fff', width: 1}}, hovertemplate: 'Notas: %{x}<br>%{y} escolas<extra></extra>'}] : [],
        {xaxis: {title: {text: dados.metricas[Number(filtro.metrica)][1]}, automargin: true}, yaxis: {title: {text: 'Escolas'}, automargin: true}});
    const grupos = r.desempenho.filter(g => g.media !== null).sort((a, b) => b.media - a.media);
    grafico('grafico-local', grupos.length ? [{type: 'bar', orientation: 'h', y: grupos.map(g => g.grupo), x: grupos.map(g => g.media),
        customdata: grupos.map(g => [g.n, g.desvio]), marker: {color: '#60866b'},
        hovertemplate: '%{y}<br>Média: %{x:.1f}<br>n = %{customdata[0]} escolas<br>Desvio padrão: %{customdata[1]:.1f}<extra></extra>'}] : [],
        {height: 350, margin: {l: 160, r: 20, t: 15, b: 60}, xaxis: {title: {text: 'Média das médias escolares'}, automargin: true}});
    const comparacoes = r.comparacoes.filter(g => g.media !== null);
    const traces = [1, 2, 3, 4, 0].map(estado => {
        const gs = comparacoes.filter(g => g.estado === estado);
        return {type: 'bar', name: EduInfra.estados[estado], x: gs.map(g => g.rede), y: gs.map(g => g.media),
            error_y: {type: 'data', array: gs.map(g => g.desvio), visible: true, thickness: 1},
            marker: {color: cores[estado]}, customdata: gs.map(g => [g.n, g.desvio]),
            hovertemplate: '%{x} · %{fullData.name}<br>Média: %{y:.1f}<br>n = %{customdata[0]} escolas<br>Desvio padrão: %{customdata[1]:.1f}<extra></extra>'};
    }).filter(trace => trace.x.length);
    grafico('grafico-relacao', traces, {barmode: 'group', height: 420, yaxis: {title: {text: 'Média das médias escolares'}, automargin: true}});
    montarTabela('tabela-relacao', ['Rede / situação', 'Escolas com nota', 'Média', 'Desvio padrão'],
        r.comparacoes.map(g => [`${g.rede} / ${EduInfra.estados[g.estado]}`, numero.format(g.n),
            g.media === null ? 'Amostra < 10' : decimal.format(g.media), g.desvio === null ? '—' : decimal.format(g.desvio)]));
    texto('nota-amostra', `${r.gruposPequenos} grupo(s) da comparação têm menos de ${dados.minimo} escolas com nota válida e suas médias foram suprimidas.`);
    montarTabela('tabela-qualidade', ['Qualidade no recorte', 'Escolas'], [
        ['Censo no recorte', numero.format(r.censo)], ['Com ENEM identificado', numero.format(r.enem)],
        ['Com perfil de infraestrutura na referência do ENEM', numero.format(r.perfisEnem)],
        [`Sem nota válida em ${dados.metricas[Number(filtro.metrica)][1]}`, numero.format(r.escolasSemNota)],
        ['Com nota válida na métrica selecionada', numero.format(r.nota.n)]
    ]);
    texto('status-acessivel', `Painel atualizado. ${numero.format(r.censo)} escolas do Censo e ${numero.format(r.enem)} escolas com ENEM no recorte.`);
    window.resultadoPainel = r;
}
Array.from(new Set(dados.municipios.map(m => m[2]))).sort().forEach(uf => opcao(porId('uf'), uf, uf));
dados.itens.forEach((item, indice) => opcao(porId('item'), indice, item[1]));
dados.metricas.forEach((metrica, indice) => opcao(porId('metrica'), indice, metrica[1]));
const biblioteca = dados.itens.findIndex(item => item[0] === 'biblioteca');
porId('item').value = String(Math.max(0, biblioteca));
atualizarMunicipios();
porId('uf').addEventListener('change', () => {atualizarMunicipios(); atualizar();});
['municipio', 'rede', 'local', 'item', 'metrica'].forEach(id => porId(id).addEventListener('change', atualizar));
porId('limpar').addEventListener('click', () => {
    ['uf', 'rede', 'local'].forEach(id => {porId(id).value = '';});
    porId('metrica').value = '0'; porId('item').value = String(Math.max(0, biblioteca));
    atualizarMunicipios(); atualizar();
});
texto('data-exportacao', new Date(dados.gerado_em).toLocaleString('pt-BR'));
atualizar();
"""


def renderizar_html(dados):
    # Impede que nomes vindos das fontes encerrem o bloco JSON ou criem marcação.
    serializado = json.dumps(dados, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    serializado = serializado.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    cobertura = dados["cobertura"]
    taxa = 100 * cobertura["identificadas"] / cobertura["entrada"]
    html = r'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EduInfra · Infraestrutura e desempenho escolar</title>
<style>__ESTILOS__</style>
<script>__PLOTLY__</script>
</head>
<body>
<main class="shell">
<header class="topo"><div class="marca">Edu<span>Infra</span> / Observatório</div><div class="edicao">Censo Escolar e ENEM · __ANO__</div></header>
<div class="hero"><div><div class="eyebrow">Infraestrutura e desempenho escolar</div><h1>O que a estrutura das escolas revela?</h1><p>Explore as condições das escolas, as diferenças entre territórios e as associações com o desempenho no ENEM.</p></div>
<aside><strong>Duas populações, uma leitura cuidadosa.</strong><br>A infraestrutura descreve todas as escolas do Censo no recorte. As análises de notas usam as escolas que encontramos no ENEM e no Censo.</aside></div>
<div class="filtros" aria-label="Filtros do painel"><div class="campos">
<div class="campo"><label for="uf">UF</label><select id="uf"><option value="">Brasil · todas as UFs</option></select></div>
<div class="campo"><label for="municipio">Município</label><select id="municipio"></select></div>
<div class="campo"><label for="rede">Rede de ensino</label><select id="rede"><option value="">Todas as redes</option><option value="publica">Somente públicas</option><option value="1">Federal</option><option value="2">Estadual</option><option value="3">Municipal</option><option value="4">Privada</option></select></div>
<div class="campo"><label for="local">Localização</label><select id="local"><option value="">Urbana e rural</option><option value="1">Urbana</option><option value="2">Rural</option><option value="0">Não informada</option></select></div>
<div class="campo"><label for="item">Item em detalhe</label><select id="item"></select></div>
<div class="campo"><label for="metrica">Nota em detalhe</label><select id="metrica"></select></div>
</div><div class="filtro-rodape"><span id="estado-filtros">Preparando o retrato nacional…</span><button class="reset" id="limpar" type="button">Limpar filtros</button></div></div>
<nav class="navegacao" aria-label="Seções"><a href="#panorama">Panorama</a><a href="#perguntas">Perguntas e respostas</a><a href="#infraestrutura">Infraestrutura</a><a href="#desempenho">Desempenho</a><a href="#relacao">Infraestrutura × notas</a><a href="#qualidade">Cobertura e método</a></nav>
<div id="status-acessivel" class="visually-hidden" aria-live="polite"></div>
<noscript><p class="aviso">Ative o JavaScript para explorar os filtros e gráficos deste painel.</p></noscript>
<section id="panorama"><div class="eyebrow">O recorte selecionado</div><h2>Comece pelo tamanho da análise</h2><p class="introducao">Os indicadores acompanham os filtros de território, rede e localização. Ano __ANO__ · referência da infraestrutura __REFERENCIA__.</p>
<div class="kpis"><div class="kpi"><span class="rotulo">Escolas do Censo</span><strong id="n-censo">—</strong><small>Base da análise de infraestrutura</small></div><div class="kpi"><span class="rotulo">Escolas com ENEM identificado</span><strong id="n-enem">—</strong><small>Subconjunto para a análise de desempenho</small></div><div class="kpi"><span class="rotulo">Média das escolas</span><strong id="n-nota">—</strong><small id="n-nota-base"></small></div><div class="kpi"><span class="rotulo">Com perfil de infraestrutura</span><strong id="n-perfil">—</strong><small id="n-perfil-base"></small></div></div></section>
<section id="perguntas"><div class="eyebrow">Leituras do recorte</div><h2>Perguntas que os dados respondem</h2><p class="introducao">As respostas são calculadas com os dados selecionados e mudam junto com os filtros.</p><div class="perguntas">
<article class="resposta"><h3>Quantas escolas entram nesta análise?</h3><p id="resposta-base"></p></article>
<article class="resposta"><h3>Quais itens têm mais ausência declarada?</h3><p id="resposta-deficits"></p></article>
<article class="resposta"><h3>Como está o desempenho no recorte?</h3><p id="resposta-notas"></p></article>
<article class="resposta"><h3>Há diferença com e sem o item?</h3><p id="resposta-relacao"></p></article>
<article class="resposta"><h3>Quanto da infraestrutura é desconhecido?</h3><p id="resposta-informacao"></p></article>
<article class="resposta"><h3>Quem ficou fora da junção nacional?</h3><p id="resposta-cobertura"></p></article>
</div></section>
<section id="infraestrutura"><div class="eyebrow">Condições das escolas</div><h2>Onde a infraestrutura está presente?</h2><p class="introducao" id="base-infra"></p>
<div class="grid"><article class="quadro largo"><h3>Disponibilidade de cada item</h3><p class="legenda">Percentual de todas as escolas do Censo no recorte. Informação desconhecida ocupa sua própria parcela.</p><div id="grafico-itens" class="grafico" role="img" aria-label="Distribuição das situações por item de infraestrutura"></div></article>
<article class="quadro"><h3 id="titulo-item">Item por UF</h3><p class="legenda">Proporção com presença declarada. Passe o cursor para conferir os denominadores.</p><div id="grafico-uf" class="grafico" role="img" aria-label="Presença do item selecionado por UF"></div><div class="tabela-container" id="tabela-infra"></div></article>
<article class="quadro"><h3>Como as escolas se distribuem por rede?</h3><p class="legenda">Contagem de escolas do Censo no recorte, incluindo as que não têm ENEM.</p><div id="grafico-redes" class="grafico" role="img" aria-label="Escolas do Censo por rede"></div></article></div></section>
<section id="desempenho"><div class="eyebrow">Resultados do ENEM</div><h2>Olhe além de uma média única</h2><p class="introducao" id="base-notas"></p><div class="grid">
<article class="quadro"><h3>Notas por área da prova</h3><p class="legenda">Média simples das médias escolares válidas em cada área. As escalas das áreas são diferentes; a altura não mede dificuldade da prova.</p><div id="grafico-areas" class="grafico" role="img" aria-label="Médias das escolas por área do ENEM"></div></article>
<article class="quadro"><h3>Como as médias escolares se distribuem?</h3><p class="legenda">Cada observação é uma escola. A métrica acompanha o filtro “Nota em detalhe”.</p><div id="grafico-distribuicao" class="grafico" role="img" aria-label="Histograma das médias escolares"></div></article>
<article class="quadro largo"><h3>Desempenho por rede e localização</h3><p class="legenda">Métrica selecionada, com grupos de pelo menos dez escolas com nota válida.</p><div id="grafico-local" class="grafico" role="img" aria-label="Médias das escolas por rede e localização"></div></article></div></section>
<section id="relacao"><div class="eyebrow">A pergunta central do projeto</div><h2 id="titulo-relacao">Como as notas variam com a infraestrutura?</h2><p class="introducao">Compare o mesmo item e a mesma métrica dentro de cada rede. Os filtros de UF, município e localização ajudam a detalhar o recorte.</p>
<div class="quadro"><h3>Desempenho por situação do item e rede</h3><p class="legenda">Barras de erro mostram um desvio padrão entre as médias escolares. O perfil de cada escola é o vinculado ao fato ENEM na data de referência.</p><div id="grafico-relacao" class="grafico" role="img" aria-label="Desempenho por infraestrutura dentro de cada rede"></div><p class="legenda" id="nota-amostra"></p><div class="tabela-container" id="tabela-relacao"></div></div>
<p class="aviso">As diferenças observadas representam associações. Os dados não permitem atribuir uma variação de nota à presença de um item. Não há controle de INSE ou de outros fatores socioeconômicos neste painel.</p></section>
<section id="qualidade"><div class="eyebrow">Cobertura e limites</div><h2>Saiba quem sustenta cada resultado</h2><p class="introducao">Cobertura nacional da junção ENEM __ANO__ × Censo __ANO__: <strong>__COBERTURA__</strong>. Esse total nacional permanece fixo ao aplicar filtros.</p>
<div class="grid"><article class="quadro"><h3>Do ENEM até a análise conjunta</h3><div class="tabela-container"><table><thead><tr><th scope="col">Base nacional</th><th scope="col">Escolas / códigos</th></tr></thead><tbody><tr><td>Códigos de escola na entrada do ENEM</td><td>__ENTRADA__</td></tr><tr><td>Escolas identificadas no Censo</td><td>__IDENTIFICADAS__</td></tr><tr><td>Códigos sem junção</td><td>__SEM_JUNCAO__</td></tr><tr><td>Sem junção e com menos de 10 participantes</td><td>__MASCARADAS__</td></tr></tbody></table></div><p class="legenda">Abaixo de dez participantes, o INEP mascara o código da escola. A entrada usada aqui não oferece os atributos para redistribuir códigos sem junção pelos filtros territoriais.</p></article>
<article class="quadro"><h3>O que há no recorte atual?</h3><div class="tabela-container" id="tabela-qualidade"></div><p class="legenda">Escolas sem nota permanecem nas contagens de cobertura. Informação de infraestrutura desconhecida não é contada como ausência.</p></article></div>
<details class="metodo"><summary>Como interpretar estes números</summary><p>Fontes: Censo Escolar e resultados do ENEM __ANO__, processados pela Silver e pela Gold do projeto. A infraestrutura usa os perfis históricos das vistorias e do Censo em __REFERENCIA__. O painel não usa o perfil atual para representar uma data passada.</p><p>A média geral escolar é formada pelos participantes com as cinco notas válidas. Cada área tem seu próprio conjunto de notas válidas. Os resultados do painel são médias simples das médias escolares, sem ponderação por participantes. As populações de cada área podem diferir.</p><p>Grupos com menos de dez escolas com nota válida têm suas médias suprimidas, inclusive após filtrar. Um desvio padrão mostra dispersão entre escolas e não representa intervalo de confiança. Comparações dentro da mesma rede ainda podem misturar contextos socioeconômicos diferentes.</p><p>A infraestrutura considera todas as escolas do Censo no recorte, inclusive as que não oferecem ensino médio. A análise de notas considera apenas o subconjunto identificado no ENEM. Este retrato de __ANO__ não permite medir evolução anual nem efeito causal de investimentos.</p><p>O HTML contém o retrato exportado. Para incorporar novas cargas, gere novamente com <code>eduinfra gerar-painel</code>. Os filtros e gráficos funcionam localmente, inclusive sem conexão à internet.</p></details></section>
<footer class="rodape">EduInfra AI · Censo e ENEM __ANO__ · Retrato exportado em <span id="data-exportacao"></span></footer>
</main>
<script type="application/json" id="dados-painel">__DADOS__</script>
<script id="painel-calculos">__CALCULOS__</script>
<script>__INTERFACE__</script>
</body></html>'''
    substituicoes = {
        "__ESTILOS__": ESTILOS, "__PLOTLY__": get_plotlyjs(), "__ANO__": str(dados["ano"]),
        "__REFERENCIA__": datetime.fromisoformat(dados["referencia"]).strftime("%d/%m/%Y"), "__COBERTURA__": f"{taxa:.1f}%".replace(".", ","),
        "__ENTRADA__": f"{cobertura['entrada']:,}".replace(",", "."),
        "__IDENTIFICADAS__": f"{cobertura['identificadas']:,}".replace(",", "."),
        "__SEM_JUNCAO__": f"{cobertura['sem_juncao']:,}".replace(",", "."),
        "__MASCARADAS__": f"{cobertura['mascaradas']:,}".replace(",", "."),
        "__DADOS__": serializado, "__CALCULOS__": CALCULOS, "__INTERFACE__": INTERFACE,
    }
    # Substituição em uma passada: texto das fontes não vira um novo marcador.
    return re.sub("|".join(map(re.escape, substituicoes)), lambda m: substituicoes[m.group()], html)


def gerar_painel():
    configuracao = carregar_configuracao()
    with conectar(configuracao) as conexao:
        dados = ler_dados(conexao, configuracao.ano_enem)
    destino = Path("docs/painel.html")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(renderizar_html(dados), encoding="utf-8")
    print(f"Painel exportado para {destino}")


if __name__ == "__main__":
    gerar_painel()
