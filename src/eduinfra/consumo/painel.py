"""Painel estático e sem servidor que lê só as tabelas de consumo da Gold (transformacao/models/marts/gestao)."""

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
from plotly.offline import get_plotlyjs

from eduinfra.nucleo.configuracao import carregar_configuracao

AREAS = (
    ("media_geral", "Média geral"),
    ("media_cn", "Ciências da Natureza"),
    ("media_ch", "Ciências Humanas"),
    ("media_lc", "Linguagens"),
    ("media_mt", "Matemática"),
    ("media_redacao", "Redação"),
)
TABELAS_DE_CONSUMO = (
    "desempenho_por_recorte", "comparacao_com_sem_item", "perfil_alto_baixo_desempenho",
    "infra_por_uf", "cobertura_enem", "escola_desempenho_infra", "dim_item",
)
# Mesmo valor de var('minimo_escolas') em transformacao/dbt_project.yml; aqui só aparece no texto do painel.
MINIMO_ESCOLAS = 10
DESTINO = Path("docs/painel.html")


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


def exigir_tabelas(conexao):
    presentes = {nome for (nome,) in conexao.execute(
        "select table_name from information_schema.tables where table_catalog = 'pg' and table_schema = 'gold'").fetchall()}
    ausentes = sorted(set(TABELAS_DE_CONSUMO) - presentes)
    if ausentes:
        raise RuntimeError(f"Gold de consumo ausente ({', '.join(ausentes)}): execute docker compose run --build --rm transformacao")


def valor_json(valor):
    return float(valor) if isinstance(valor, Decimal) else valor


def linhas(conexao, consulta, parametros=()):
    cursor = conexao.execute(consulta, list(parametros))
    nomes = [coluna[0] for coluna in cursor.description]
    return [{nome: valor_json(valor) for nome, valor in zip(nomes, linha)} for linha in cursor.fetchall()]


def referencia_do_ano(conexao, ano):
    datas = conexao.execute("""select distinct data_referencia_infraestrutura
        from pg.gold.escola_desempenho_infra where ano = ?""", [ano]).fetchall()
    if len(datas) != 1:
        raise RuntimeError(f"A Gold de {ano} precisa ter exatamente uma data de referência de infraestrutura; tem {len(datas)}")
    return datas[0][0]


def escolas_alto_baixo(conexao, ano, itens):
    # Linhas em lista e itens pelo índice em "itens": milhares de escolas cabem no HTML sem repetir nomes de campo.
    posicao = {codigo: indice for indice, (codigo, *_) in enumerate(itens)}
    escolas = conexao.execute("""select no_entidade, no_municipio, sg_uf, rede, faixa_desempenho, media_geral, itens_existentes
        from pg.gold.escola_desempenho_infra where ano = ? and faixa_desempenho in ('alto', 'baixo')
        order by media_geral desc, co_entidade""", [ano]).fetchall()
    return [[nome, municipio, uf, rede, faixa, valor_json(media), sorted(posicao[codigo] for codigo in existentes if codigo in posicao)]
            for nome, municipio, uf, rede, faixa, media, existentes in escolas]


def ler_dados(conexao, ano):
    exigir_tabelas(conexao)
    referencia = referencia_do_ano(conexao, ano)
    cobertura = linhas(conexao, "select * exclude (ano) from pg.gold.cobertura_enem where ano = ?", [ano])
    if len(cobertura) != 1:
        raise RuntimeError(f"Cobertura do ENEM {ano} ausente na Gold; reexecute transformacao")
    itens = conexao.execute("select item_codigo, descricao, categoria from pg.gold.dim_item order by descricao").fetchall()
    return {
        "ano": ano, "referencia": str(referencia), "minimo": MINIMO_ESCOLAS,
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "itens": [list(item) for item in itens], "areas": AREAS,
        "ufs": [uf for (uf,) in conexao.execute("""select distinct uf from pg.gold.infra_por_uf
            where ano = ? and uf not in ('BR', 'NI') order by uf""", [ano]).fetchall()],
        "recortes": linhas(conexao, """select uf, rede, area, n_escolas, n_escolas_com_nota, media, desvio_padrao
            from pg.gold.desempenho_por_recorte where ano = ?""", [ano]),
        "comparacoes": linhas(conexao, """select uf, rede, item_codigo, area, media_com_item, n_com_item,
            media_sem_item, n_sem_item, diferenca
            from pg.gold.comparacao_com_sem_item where ano = ? and n_com_item + n_sem_item > 0""", [ano]),
        "perfil": linhas(conexao, """select rede, area, tipo, caracteristica, descricao, n_alto, n_alto_conhecido, pct_alto,
            n_baixo, n_baixo_conhecido, pct_baixo, diferenca_pp, suprimido, ordem
            from pg.gold.perfil_alto_baixo_desempenho where ano = ? order by rede, area, ordem""", [ano]),
        "infra": linhas(conexao, """select uf, rede, item_codigo, situacao, n_escolas, pct_das_escolas
            from pg.gold.infra_por_uf where ano = ?""", [ano]),
        "escolas_alto_baixo": escolas_alto_baixo(conexao, ano, itens),
        "cobertura": cobertura[0],
    }


CALCULOS = r"""
'use strict';
const EduInfra = (() => {
    const redes = {todas: 'todas as redes', federal: 'rede federal', estadual: 'rede estadual', municipal: 'rede municipal', privada: 'rede privada'};
    const naRede = {todas: 'em todas as redes', federal: 'na rede federal', estadual: 'na rede estadual', municipal: 'na rede municipal', privada: 'na rede privada'};
    const decimal = new Intl.NumberFormat('pt-BR', {minimumFractionDigits: 1, maximumFractionDigits: 1});
    const inteiro = new Intl.NumberFormat('pt-BR');
    const ITENS_EM_DESTAQUE = 3;
    const pontos = valor => `${decimal.format(Math.abs(valor))} ${Math.abs(valor) === 1 ? 'ponto' : 'pontos'}`;
    const juntar = nomes => nomes.length < 2 ? nomes.join('') : `${nomes.slice(0, -1).join(', ')} e ${nomes[nomes.length - 1]}`;
    function nomeDoItem(dados, codigo) {
        const item = dados.itens.find(i => i[0] === codigo);
        return item ? item[1] : codigo;
    }
    function nomeDaArea(dados, codigo) {
        const area = dados.areas.find(a => a[0] === codigo);
        return area ? area[1] : codigo;
    }
    const desempenho = (dados, area) => area === 'media_geral' ? 'desempenho' : `desempenho em ${nomeDaArea(dados, area)}`;
    const temAs = {
        item: (dados, p) => `têm ${nomeDoItem(dados, p.caracteristica).toLowerCase()}`,
        localizacao: () => 'ficam em área urbana',
        uf: (dados, p) => `ficam em ${p.caracteristica}`,
        conjunto_de_itens: () => 'têm pelo menos metade dos itens pesquisados',
    };
    const ressalvas = {
        item: 'Isso mostra o que as escolas de nota alta costumam ter. Não quer dizer que instalar o item, sozinho, eleve a nota.',
        localizacao: 'Localização e estado costumam vir junto com diferenças de renda das famílias, que este painel não controla.',
        uf: 'Localização e estado costumam vir junto com diferenças de renda das famílias, que este painel não controla.',
        conjunto_de_itens: 'Escolas com mais itens costumam atender famílias de maior renda, que este painel não controla.',
    };
    function recorte(dados, filtro) {
        const uf = filtro.uf || 'BR', rede = filtro.rede || 'todas', area = filtro.area || 'media_geral';
        const geral = dados.recortes.find(r => r.uf === uf && r.rede === rede && r.area === area)
            || {n_escolas: 0, n_escolas_com_nota: 0, media: null, desvio_padrao: null};
        const comparacoes = dados.comparacoes.filter(c => c.uf === uf && c.rede === rede && c.area === area)
            .sort((a, b) => (b.diferenca ?? -Infinity) - (a.diferenca ?? -Infinity));
        const perfil = dados.perfil.filter(p => p.rede === rede && p.area === area).sort((a, b) => a.ordem - b.ordem);
        const infra = dados.infra.filter(i => i.uf === uf && i.rede === rede);
        // Cada item cobre todas as escolas do recorte (uma linha por situação), então um item basta para contar.
        const referencia = infra.length ? infra[0].item_codigo : null;
        const censo = infra.filter(i => i.item_codigo === referencia).reduce((total, i) => total + i.n_escolas, 0);
        return {uf, rede, area, geral, comparacoes, perfil, infra, censo};
    }
    function fraseDoPerfil(dados, r, p) {
        const faixa = desempenho(dados, r.area);
        return {
            texto: `No Brasil, ${naRede[r.rede]}, ${decimal.format(p.pct_alto)}% das escolas de maior ${faixa} ${temAs[p.tipo](dados, p)}, contra ${decimal.format(p.pct_baixo)}% das de menor ${faixa}.`,
            ressalva: ressalvas[p.tipo]
        };
    }
    function conclusoes(dados, filtro, limite = 2) {
        const r = recorte(dados, filtro);
        const lista = [];
        const comparaveis = r.perfil.filter(p => !p.suprimido && p.diferenca_pp !== null);
        const itens = comparaveis.filter(p => p.tipo === 'item' && p.diferenca_pp > 0);
        if (itens.length) lista.push({
            rotulo: 'Onde olhar primeiro',
            texto: `No Brasil, ${naRede[r.rede]}, os itens que mais separam as escolas de maior e de menor ${desempenho(dados, r.area)} são ${juntar(itens.slice(0, ITENS_EM_DESTAQUE).map(p => nomeDoItem(dados, p.caracteristica).toLowerCase()))}.`,
            ressalva: 'A ordem segue a diferença entre as duas faixas. Não quer dizer que o item explique a nota sozinho.'
        });
        itens.slice(0, limite).forEach(p => lista.push(fraseDoPerfil(dados, r, p)));
        const contexto = comparaveis.find(p => p.tipo !== 'item' && p.diferenca_pp !== 0);
        if (contexto) lista.push(fraseDoPerfil(dados, r, contexto));
        const comparavel = r.comparacoes.filter(c => c.diferenca !== null);
        if (comparavel.length) {
            const maior = comparavel[0];
            lista.push({
                texto: `${r.uf === 'BR' ? 'No Brasil' : `Em ${r.uf}`}, ${naRede[r.rede]}, a maior diferença de nota aparece em ${nomeDoItem(dados, maior.item_codigo).toLowerCase()}: escolas com o item têm média ${maior.diferenca >= 0 ? 'maior' : 'menor'} em ${pontos(maior.diferenca)} (${inteiro.format(maior.n_com_item)} escolas com, ${inteiro.format(maior.n_sem_item)} sem).`,
                ressalva: 'A comparação não controla o nível socioeconômico das escolas, que costuma andar junto com a infraestrutura.'
            });
        }
        if (!lista.length) lista.push({
            texto: `Este recorte não tem escolas suficientes para comparar. Cada grupo precisa de pelo menos ${dados.minimo} escolas com nota.`,
            ressalva: 'Escolha Brasil ou todas as redes para ver o resultado nacional.'
        });
        return lista;
    }
    function escolas(dados, filtro) {
        const uf = filtro.uf || 'BR', rede = filtro.rede || 'todas';
        const doRecorte = dados.escolas_alto_baixo
            .map(([nome, municipio, sigla, redeDaEscola, faixa, media, itens]) => ({nome, municipio, uf: sigla, rede: redeDaEscola, faixa, media, itens}))
            .filter(e => (uf === 'BR' || e.uf === uf) && (rede === 'todas' || e.rede === rede));
        // Mesma supressão das médias: a faixa com menos escolas que o mínimo não é listada.
        const daFaixa = faixa => {
            const lista = doRecorte.filter(e => e.faixa === faixa);
            return lista.length >= dados.minimo ? lista : [];
        };
        return {alto: daFaixa('alto'), baixo: daFaixa('baixo').reverse()};
    }
    function csv(dados, filtro) {
        const {alto, baixo} = escolas(dados, filtro);
        // Prefixo em texto que começa com sinal de fórmula, para a planilha não executar o nome da escola.
        const campo = valor => {
            const texto = String(valor ?? '');
            return `"${(/^[=+\-@]/.test(texto) ? `'${texto}` : texto).replace(/"/g, '""')}"`;
        };
        const linhas = [['escola', 'municipio', 'uf', 'rede', 'faixa', 'media_geral', 'itens_existentes']].concat(
            alto.concat(baixo).map(e => [e.nome, e.municipio, e.uf, e.rede, e.faixa === 'alto' ? 'maior desempenho' : 'menor desempenho',
                decimal.format(e.media), e.itens.map(i => dados.itens[i][1]).join(', ')]));
        // BOM e ponto e vírgula: o Excel em português abre com acentos e colunas no lugar.
        return '﻿' + linhas.map(l => l.map(campo).join(';')).join('\r\n');
    }
    return {recorte, conclusoes, escolas, csv, nomeDoItem, nomeDaArea, redes};
})();
if (typeof module !== 'undefined') module.exports = EduInfra;
"""

ESTILOS = """
:root{--fundo:#f5f6f1;--papel:#fff;--tinta:#20392f;--verde:#1c6650;--muted:#5b6d62;--linha:#dce3d9;--alto:#1c6650;--baixo:#b65c43}
*{box-sizing:border-box}body{margin:0;background:var(--fundo);color:var(--tinta);font:16px/1.55 system-ui,'Segoe UI',sans-serif}
.shell{max-width:1200px;margin:auto;padding:0 32px 64px}.topo{display:flex;justify-content:space-between;align-items:center;padding:22px 0;border-bottom:1px solid var(--linha);gap:16px}
.marca{font-size:20px;font-weight:800}.marca span{color:var(--verde)}.edicao{font-size:13px;color:var(--muted)}
h1{font-size:clamp(28px,3.2vw,44px);line-height:1.15;margin:32px 0 8px;font-weight:700}h2{font-size:24px;margin:0 0 6px}h3{font-size:16px;margin:0 0 6px}
.subtitulo{color:var(--muted);margin:0 0 20px;max-width:760px}
.resposta{background:var(--papel);border:1px solid var(--linha);border-left:5px solid var(--verde);border-radius:10px;padding:22px 26px;margin:18px 0}
.resposta ol{margin:8px 0 0;padding-left:22px}.resposta li{margin:0 0 14px}.resposta li p{margin:0}.ressalva{font-size:13px;color:var(--muted);margin-top:3px!important}
.filtros{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;background:var(--papel);border:1px solid var(--linha);border-radius:10px;padding:14px 16px;position:sticky;top:0;z-index:3}
label{display:block;font-size:12px;font-weight:700;color:var(--muted);margin-bottom:4px}select{width:100%;min-height:40px;padding:6px 8px;border:1px solid #c6d0c3;border-radius:6px;font:inherit;font-size:14px;background:#fff;color:var(--tinta)}
select:focus-visible{outline:3px solid #c47c39;outline-offset:2px}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:18px 0}.kpi{background:var(--papel);border:1px solid var(--linha);border-radius:10px;padding:18px}.kpi strong{display:block;font-size:30px;font-weight:700}.kpi span{font-size:13px;color:var(--muted)}
section{margin-top:40px}.quadro{background:var(--papel);border:1px solid var(--linha);border-radius:10px;padding:20px;margin-top:14px}.legenda{font-size:13px;color:var(--muted);margin:0 0 8px}
.grafico{min-height:320px}.vazio{color:var(--muted);font-size:14px;padding:24px 0}
details{margin-top:14px;background:#eaf0e7;border-radius:10px;padding:16px 20px;font-size:14px}summary{cursor:pointer;font-weight:700}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}th,td{padding:8px;border-bottom:1px solid var(--linha);text-align:right}th:first-child,td:first-child{text-align:left}.tabela{overflow-x:auto}
button{min-height:40px;padding:8px 16px;border:1px solid var(--verde);border-radius:6px;background:var(--verde);color:#fff;font:inherit;font-size:14px;font-weight:700;cursor:pointer}button:disabled{background:#c6d0c3;border-color:#c6d0c3;cursor:not-allowed}button:focus-visible{outline:3px solid #c47c39;outline-offset:2px}
.subsecao{margin-top:24px}.colunas{display:grid;grid-template-columns:1fr 1fr;gap:14px}dl{margin:0}dt{font-weight:700;margin-top:10px}dd{margin:2px 0 0}
.aviso{background:#fff2de;border:1px solid #ebd6b5;border-radius:8px;padding:12px 16px;font-size:14px;margin-top:14px}.rodape{margin-top:32px;font-size:12px;color:var(--muted)}
@media(max-width:760px){.shell{padding:0 16px 40px}.filtros{grid-template-columns:1fr;position:static}.kpis{grid-template-columns:1fr}.colunas{grid-template-columns:1fr}}
"""

INTERFACE = r"""
'use strict';
const dados = JSON.parse(document.getElementById('dados-painel').textContent);
const decimal = new Intl.NumberFormat('pt-BR', {minimumFractionDigits: 1, maximumFractionDigits: 1});
const inteiro = new Intl.NumberFormat('pt-BR');
const ESCOLAS_NA_TELA = 20;
const UFS_NO_CONTEXTO = 5;
const COR_ALTO = '#1c6650', COR_BAIXO = '#b65c43';
const porId = id => document.getElementById(id);
const nomeDaArea = codigo => EduInfra.nomeDaArea(dados, codigo);
function opcao(select, valor, rotulo) { const o = document.createElement('option'); o.value = valor; o.textContent = rotulo; select.add(o); }
function filtro() { return {uf: porId('uf').value, rede: porId('rede').value, area: porId('area').value, item: porId('item').value}; }
function grafico(id, traces, layout) {
    const elemento = porId(id);
    if (!traces.length) { Plotly.purge(elemento); elemento.innerHTML = '<p class="vazio">Sem escolas suficientes neste recorte. Amplie para Brasil ou todas as redes.</p>'; return; }
    elemento.querySelectorAll('.vazio').forEach(p => p.remove());
    Plotly.react(elemento, traces, Object.assign({paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', separators: ',.',
        font: {family: 'system-ui, sans-serif', color: '#20392f', size: 12}, margin: {l: 10, r: 20, t: 10, b: 50},
        yaxis: {automargin: true}, legend: {orientation: 'h', y: -0.15}}, layout), {responsive: true, displaylogo: false});
}
function tabela(id, cabecalho, corpo) {
    const t = document.createElement('table'); const h = t.createTHead().insertRow();
    cabecalho.forEach(c => { const th = document.createElement('th'); th.textContent = c; h.appendChild(th); });
    const b = t.createTBody(); corpo.forEach(l => { const tr = b.insertRow(); l.forEach(v => { tr.insertCell().textContent = v; }); });
    porId(id).replaceChildren(t);
}
// A faixa de menor desempenho leva hachura, para as duas barras não se distinguirem só pela cor.
function barrasDoPerfil(linhas, rotulo) {
    if (!linhas.length) return [];
    const nomes = linhas.map(rotulo);
    return [
        {type: 'bar', orientation: 'h', name: 'Escolas de menor desempenho', y: nomes, x: linhas.map(p => p.pct_baixo),
         marker: {color: COR_BAIXO, pattern: {shape: '/', fgcolor: '#ffffff', solidity: 0.35}},
         customdata: linhas.map(p => p.n_baixo_conhecido), hovertemplate: '%{y}<br>%{x:.1f}% das %{customdata} escolas de menor desempenho<extra></extra>'},
        {type: 'bar', orientation: 'h', name: 'Escolas de maior desempenho', y: nomes, x: linhas.map(p => p.pct_alto), marker: {color: COR_ALTO},
         customdata: linhas.map(p => p.n_alto_conhecido), hovertemplate: '%{y}<br>%{x:.1f}% das %{customdata} escolas de maior desempenho<extra></extra>'}
    ];
}
function listaDeEscolas(id, lista, descricao) {
    if (!lista.length) { porId(id).innerHTML = `<p class="vazio">Menos de ${dados.minimo} escolas de ${descricao} neste recorte.</p>`; return; }
    tabela(id, ['Escola', 'Município', 'UF', 'Rede', 'Média geral', 'Itens que tem'], lista.slice(0, ESCOLAS_NA_TELA).map(e => [
        e.nome, e.municipio || '—', e.uf, e.rede, decimal.format(e.media), e.itens.map(i => dados.itens[i][1]).join(', ') || 'nenhum informado']));
}
function atualizar() {
    const f = filtro(); const r = EduInfra.recorte(dados, f);
    const lista = porId('conclusoes'); lista.replaceChildren();
    EduInfra.conclusoes(dados, f).forEach(c => {
        const li = document.createElement('li'); const p = document.createElement('p');
        if (c.rotulo) { const rotulo = document.createElement('strong'); rotulo.textContent = `${c.rotulo}. `; p.appendChild(rotulo); }
        p.append(c.texto);
        const q = document.createElement('p'); q.className = 'ressalva'; q.textContent = `O que isso não quer dizer: ${c.ressalva}`;
        li.append(p, q); lista.appendChild(li);
    });
    porId('kpi-censo').textContent = inteiro.format(r.censo);
    porId('kpi-enem').textContent = inteiro.format(r.geral.n_escolas_com_nota);
    porId('kpi-media').textContent = r.geral.media === null ? '—' : decimal.format(r.geral.media);
    porId('kpi-media-rotulo').textContent = `${nomeDaArea(r.area)} das escolas`;
    porId('rotulo-perfil').textContent = `${EduInfra.redes[r.rede]}, Brasil, ${nomeDaArea(r.area)}.`;
    const comparaveis = r.perfil.filter(p => !p.suprimido);
    const itens = comparaveis.filter(p => p.tipo === 'item').reverse();
    grafico('grafico-perfil', barrasDoPerfil(itens, p => EduInfra.nomeDoItem(dados, p.caracteristica)),
        {barmode: 'group', height: Math.max(360, itens.length * 42 + 80), xaxis: {range: [0, 100], ticksuffix: '%', title: {text: '% das escolas que têm o item'}}});
    const ufs = comparaveis.filter(p => p.tipo === 'uf').slice(0, UFS_NO_CONTEXTO);
    const contexto = comparaveis.filter(p => p.tipo !== 'item' && p.tipo !== 'uf').concat(ufs).reverse();
    grafico('grafico-contexto', barrasDoPerfil(contexto, p => p.descricao),
        {barmode: 'group', height: Math.max(300, contexto.length * 42 + 80), xaxis: {range: [0, 100], ticksuffix: '%', title: {text: '% das escolas com a característica'}}});
    const diferencas = r.comparacoes.filter(c => c.diferenca !== null).slice().reverse();
    grafico('grafico-comparacao', diferencas.length ? [{type: 'bar', orientation: 'h', y: diferencas.map(c => EduInfra.nomeDoItem(dados, c.item_codigo)),
        x: diferencas.map(c => c.diferenca), marker: {color: diferencas.map(c => c.diferenca >= 0 ? COR_ALTO : COR_BAIXO)},
        customdata: diferencas.map(c => [c.media_com_item, c.n_com_item, c.media_sem_item, c.n_sem_item]),
        hovertemplate: '%{y}<br>Com o item: %{customdata[0]:.1f} (%{customdata[1]} escolas)<br>Sem o item: %{customdata[2]:.1f} (%{customdata[3]} escolas)<br>Diferença: %{x:.1f} pontos<extra></extra>'}] : [],
        {height: Math.max(360, diferencas.length * 28 + 80), xaxis: {title: {text: `Diferença na ${nomeDaArea(r.area).toLowerCase()} (pontos)`}, zeroline: true}});
    porId('rotulo-comparacao').textContent = `${r.uf === 'BR' ? 'Brasil' : r.uf}, ${EduInfra.redes[r.rede]}, ${nomeDaArea(r.area)}.`;
    const item = f.item; const nomeItem = EduInfra.nomeDoItem(dados, item);
    porId('titulo-uf').textContent = `Onde as escolas têm ${nomeItem.toLowerCase()}?`;
    const porUf = dados.ufs.map(uf => ({uf, linha: dados.infra.find(i => i.uf === uf && i.rede === r.rede && i.item_codigo === item && i.situacao === 'existente')}))
        .filter(u => u.linha).sort((a, b) => b.linha.pct_das_escolas - a.linha.pct_das_escolas);
    grafico('grafico-uf', porUf.length ? [{type: 'bar', x: porUf.map(u => u.uf), y: porUf.map(u => u.linha.pct_das_escolas), marker: {color: COR_ALTO},
        customdata: porUf.map(u => u.linha.n_escolas), hovertemplate: '%{x}: %{y:.1f}% das escolas do Censo (%{customdata} escolas)<extra></extra>'}] : [],
        {height: 340, margin: {l: 50, r: 20, t: 10, b: 40}, yaxis: {range: [0, 100], ticksuffix: '%'}});
    const extremos = EduInfra.escolas(dados, f);
    listaDeEscolas('lista-alto', extremos.alto, 'maior desempenho');
    listaDeEscolas('lista-baixo', extremos.baixo, 'menor desempenho');
    porId('rotulo-escolas').textContent = `${r.uf === 'BR' ? 'Brasil' : r.uf}, ${EduInfra.redes[r.rede]}. A tela mostra até ${ESCOLAS_NA_TELA} de cada faixa; o arquivo traz as ${inteiro.format(extremos.alto.length + extremos.baixo.length)} escolas.`;
    porId('baixar-csv').disabled = !(extremos.alto.length + extremos.baixo.length);
    tabela('tabela-detalhes', ['Item', 'Escolas com', 'Média com', 'Escolas sem', 'Média sem', 'Diferença'],
        r.comparacoes.map(c => [EduInfra.nomeDoItem(dados, c.item_codigo), inteiro.format(c.n_com_item),
            c.media_com_item === null ? `menos de ${dados.minimo}` : decimal.format(c.media_com_item), inteiro.format(c.n_sem_item),
            c.media_sem_item === null ? `menos de ${dados.minimo}` : decimal.format(c.media_sem_item), c.diferenca === null ? '—' : decimal.format(c.diferenca)]));
    tabela('tabela-perfil', ['Característica', 'Maior desempenho', 'Menor desempenho', 'Diferença (p.p.)'],
        comparaveis.map(p => [p.tipo === 'item' ? EduInfra.nomeDoItem(dados, p.caracteristica) : p.descricao,
            `${decimal.format(p.pct_alto)}% de ${inteiro.format(p.n_alto_conhecido)}`, `${decimal.format(p.pct_baixo)}% de ${inteiro.format(p.n_baixo_conhecido)}`, decimal.format(p.diferenca_pp)]));
    porId('desvio').textContent = r.geral.desvio_padrao === null ? '—' : decimal.format(r.geral.desvio_padrao);
}
function baixarCsv() {
    const f = filtro();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([EduInfra.csv(dados, f)], {type: 'text/csv;charset=utf-8'}));
    link.download = `escolas-maior-menor-desempenho-${f.uf}-${f.rede}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
}
dados.ufs.forEach(uf => opcao(porId('uf'), uf, uf));
dados.areas.forEach(a => opcao(porId('area'), a[0], a[1]));
dados.itens.forEach(i => opcao(porId('item'), i[0], i[1]));
porId('item').value = dados.itens.some(i => i[0] === 'biblioteca') ? 'biblioteca' : dados.itens[0][0];
['uf', 'rede', 'area', 'item'].forEach(id => porId(id).addEventListener('change', atualizar));
porId('baixar-csv').addEventListener('click', baixarCsv);
atualizar();
"""

HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EduInfra · Infraestrutura e desempenho escolar</title>
<style>__ESTILOS__</style>
<script>__PLOTLY__</script>
</head>
<body>
<main class="shell">
<header class="topo"><div class="marca">Edu<span>Infra</span></div><div class="edicao">Censo Escolar e ENEM __ANO__</div></header>
<h1>O que as escolas de melhor desempenho no ENEM têm que as outras não têm?</h1>
<p class="subtitulo">Resposta calculada na camada Gold do projeto, com as escolas do ENEM __ANO__ encontradas no Censo Escolar. As frases abaixo mudam com os filtros.</p>
<div class="filtros" aria-label="Filtros">
<div><label for="uf">Estado</label><select id="uf"><option value="BR">Brasil</option></select></div>
<div><label for="rede">Rede de ensino</label><select id="rede"><option value="todas">Todas as redes</option><option value="estadual">Estadual</option><option value="municipal">Municipal</option><option value="federal">Federal</option><option value="privada">Privada</option></select></div>
<div><label for="area">Nota</label><select id="area"></select></div>
</div>
<div class="resposta" aria-live="polite"><h2>Resposta</h2><ol id="conclusoes"></ol></div>
<div class="kpis"><div class="kpi"><strong id="kpi-censo">—</strong><span>escolas do Censo no recorte</span></div><div class="kpi"><strong id="kpi-enem">—</strong><span>escolas com nota do ENEM</span></div><div class="kpi"><strong id="kpi-media">—</strong><span id="kpi-media-rotulo">Média das escolas</span></div></div>
<section><h2>O que diferencia as escolas de maior e de menor desempenho</h2>
<p class="subtitulo">Maior desempenho são os 20% das escolas com nota mais alta na própria rede, na nota escolhida no filtro; menor, os 20% com nota mais baixa. Cada barra mostra quantas delas têm o item. <span id="rotulo-perfil"></span></p>
<div class="quadro"><div id="grafico-perfil" class="grafico" role="img" aria-label="Percentual de escolas com cada item nas faixas de maior e menor desempenho"></div></div>
<h3 class="subsecao">Além dos itens</h3>
<p class="legenda">Localização, os cinco estados que mais diferenciam as duas faixas e escolas com pelo menos metade dos itens pesquisados.</p>
<div class="quadro"><div id="grafico-contexto" class="grafico" role="img" aria-label="Localização, estado e quantidade de itens nas faixas de maior e menor desempenho"></div></div></section>
<section><h2>Quanto a nota muda entre escolas com e sem cada item</h2>
<p class="subtitulo">Diferença entre a média das escolas que têm o item e a das que declararam não ter. <span id="rotulo-comparacao"></span></p>
<div class="quadro"><div id="grafico-comparacao" class="grafico" role="img" aria-label="Diferença de nota entre escolas com e sem cada item"></div></div>
<p class="aviso">São associações. Escolas com mais infraestrutura costumam atender alunos de maior renda, e o painel não controla o nível socioeconômico (o INSE não entra no cálculo).</p></section>
<section><h2>Escolas de maior e de menor desempenho</h2>
<p class="subtitulo">As escolas de cada faixa pela média geral, com os itens que declararam ter no Censo. <span id="rotulo-escolas"></span></p>
<button type="button" id="baixar-csv">Baixar a lista em CSV</button>
<div class="colunas"><div class="quadro"><h3>Maior desempenho</h3><div class="tabela" id="lista-alto"></div></div>
<div class="quadro"><h3>Menor desempenho</h3><div class="tabela" id="lista-baixo"></div></div></div></section>
<section><h2 id="titulo-uf">Onde as escolas têm o item?</h2>
<p class="subtitulo">Todas as escolas do Censo, inclusive as sem ENEM, com o perfil de infraestrutura de __REFERENCIA__.</p>
<div><label for="item">Item</label><select id="item"></select></div>
<div class="quadro"><div id="grafico-uf" class="grafico" role="img" aria-label="Percentual de escolas com o item por estado"></div></div></section>
<section><h2>Cobertura e método</h2>
<p class="subtitulo">Das __ENEM__ escolas com código no ENEM, __IDENTIFICADAS__ (__COBERTURA__) foram encontradas no Censo. __MASCARADAS__ das que ficaram de fora têm menos de dez participantes, e o INEP mascara o código delas.</p>
<details><summary>Detalhes para quem é de dados</summary>
<p>Cada média é a média simples das médias das escolas, sem ponderar pelo número de participantes. Grupos com menos de __MINIMO__ escolas com nota não têm média publicada. A faixa de desempenho é o quintil da nota escolhida dentro da rede; a lista de escolas usa a média geral. Desvio padrão entre as escolas do recorte: <strong id="desvio">—</strong> pontos.</p>
<p>As tabelas vêm de transformacao/models/marts/gestao e são as mesmas que o ETL reverso e as consultas por SQL leem.</p>
<div class="tabela" id="tabela-detalhes"></div>
<p>Tudo o que diferencia as duas faixas, em ordem, com o número de escolas em que a característica é conhecida:</p>
<div class="tabela" id="tabela-perfil"></div></details></section>
<section><h2>Termos usados</h2><dl>
<dt>Rede</dt><dd>Quem mantém a escola: federal, estadual, municipal ou privada. As faixas de desempenho são calculadas dentro de cada rede, para não comparar escola privada com pública.</dd>
<dt>Situação do item</dt><dd>O que a escola declarou ao Censo Escolar: tem o item, não tem, ou não informou. Quem não informou fica fora das contas, em vez de ser tratado como quem não tem.</dd>
<dt>Escolas com nota</dt><dd>Escolas cujo código aparece nos microdados do ENEM e foi encontrado no Censo. Escolas com menos de dez participantes têm o código mascarado pelo INEP e ficam de fora.</dd>
<dt>Maior e menor desempenho</dt><dd>Os 20% de escolas com a nota mais alta e os 20% com a nota mais baixa da própria rede.</dd>
</dl></section>
<footer class="rodape">EduInfra AI · gerado em __GERADO__ a partir da Gold</footer>
</main>
<script type="application/json" id="dados-painel">__DADOS__</script>
<script id="painel-calculos">__CALCULOS__</script>
<script>__INTERFACE__</script>
</body></html>"""


def milhar(valor):
    return f"{int(valor):,}".replace(",", ".")


def renderizar_html(dados):
    # Impede que nomes vindos das fontes encerrem o bloco JSON ou criem marcação.
    serializado = json.dumps(dados, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    serializado = serializado.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    cobertura = dados["cobertura"]
    substituicoes = {
        "__ESTILOS__": ESTILOS, "__PLOTLY__": get_plotlyjs(), "__ANO__": str(dados["ano"]),
        "__REFERENCIA__": datetime.fromisoformat(dados["referencia"]).strftime("%d/%m/%Y"),
        "__ENEM__": milhar(cobertura["n_codigos_enem"]), "__IDENTIFICADAS__": milhar(cobertura["n_identificadas_no_censo"]),
        "__COBERTURA__": f"{cobertura['pct_identificadas']:.1f}%".replace(".", ","),
        "__MASCARADAS__": milhar(cobertura["n_sem_juncao_poucos_participantes"]), "__MINIMO__": str(dados["minimo"]),
        "__GERADO__": datetime.fromisoformat(dados["gerado_em"]).strftime("%d/%m/%Y"),
        "__DADOS__": serializado, "__CALCULOS__": CALCULOS, "__INTERFACE__": INTERFACE,
    }
    # Substituição em uma passada: texto das fontes não vira um novo marcador.
    return re.sub("|".join(map(re.escape, substituicoes)), lambda m: substituicoes[m.group()], HTML)


def gerar_painel():
    configuracao = carregar_configuracao()
    with conectar(configuracao) as conexao:
        dados = ler_dados(conexao, configuracao.ano_enem)
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(renderizar_html(dados), encoding="utf-8")
    print(f"Painel exportado para {DESTINO}")


if __name__ == "__main__":
    gerar_painel()
