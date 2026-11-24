"""Garante consistência entre o painel e a camada semântica (issue #84)."""

import json
import os
import shutil
import subprocess
from statistics import mean, stdev

import psycopg
import pytest

from eduinfra import painel, semantica


def _calcular_painel(dados, **filtros):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node necessário para executar os cálculos do painel")
    programa = painel.CALCULOS + """
        const entrada = JSON.parse(require('fs').readFileSync(0, 'utf8'));
        console.log(JSON.stringify(EduInfra.calcular(entrada.dados, entrada.filtros)));
    """
    res = subprocess.run(
        [node, "-e", programa],
        input=json.dumps({"dados": dados, "filtros": filtros}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(res.stdout)


def _dados_fixture():
    municipios = [[5300108, "Brasília", "DF"], [3550308, "São Paulo", "SP"]]
    itens = [["biblioteca", "Biblioteca"], ["internet", "Internet"]]
    escolas = []
    enem = []
    for i in range(40):
        mun = 0 if i < 20 else 1
        rede = 2 if i % 2 == 0 else 4  # estadual / privada
        estado_bib = "1" if (i // 2) % 2 == 0 else "2"
        cod = estado_bib + "1"
        escolas.append([mun, rede, 1, cod, True])
        nota = 500.0 + (i * 3.0)
        enem.append([i, cod, [nota, nota, nota, nota, nota, nota], 15, True])
    return {
        "ano": 2024,
        "referencia": "2024-11-03",
        "minimo": int(semantica.carregar_definicoes()["media_enem_escolas"]["supressao"]["minimo"]),
        "versoes_metricas": {k: str(v["versao"]) for k, v in semantica.carregar_definicoes().items()},
        "gerado_em": "2026-11-24T12:00:00+00:00",
        "itens": itens,
        "metricas": list(painel.METRICAS),
        "municipios": municipios,
        "escolas": escolas,
        "enem": enem,
        "cobertura": {"entrada": 42, "identificadas": 40, "sem_juncao": 2, "mascaradas": 2},
    }


def _oraculo_semantico(dados, uf=None, rede=None):
    minimo = int(semantica.carregar_definicoes()["media_enem_escolas"]["supressao"]["minimo"])
    notas = []
    for reg in dados["enem"]:
        escola = dados["escolas"][reg[0]]
        mun = dados["municipios"][escola[0]]
        if uf and mun[2] != uf:
            continue
        if rede and escola[1] != int(rede):
            continue
        notas.append(reg[2][0])
    if len(notas) < minimo:
        return {"n": len(notas), "media": None, "desvio": None}
    return {"n": len(notas), "media": mean(notas), "desvio": stdev(notas)}


@pytest.mark.parametrize("filtros", [
    {},
    {"uf": "DF"},
    {"uf": "SP"},
    {"rede": "2"},
    {"uf": "DF", "rede": "2"},
    {"uf": "SP", "rede": "4"},
])
def test_painel_e_definicao_semantica_retornam_mesmo_valor(filtros):
    dados = _dados_fixture()
    res_painel = _calcular_painel(dados, **filtros)["nota"]
    res_semantica = _oraculo_semantico(dados, uf=filtros.get("uf"), rede=filtros.get("rede"))
    assert res_painel["n"] == res_semantica["n"]
    if res_semantica["media"] is None:
        assert res_painel["media"] is None
    else:
        assert res_painel["media"] == pytest.approx(res_semantica["media"])
        assert res_painel["desvio"] == pytest.approx(res_semantica["desvio"])


def test_divergencia_em_um_consumidor_derruba_o_teste():
    dados = _dados_fixture()
    # Simula alteração isolada da regra de supressão apenas no painel
    dados_divergentes = {**dados, "minimo": 50}
    res_painel = _calcular_painel(dados_divergentes)["nota"]
    res_semantica = _oraculo_semantico(dados)
    assert res_painel["media"] != res_semantica["media"]


@pytest.mark.integracao
@pytest.mark.parametrize("uf,rede", [(None, None), ("DF", None), (None, "estadual")])
def test_integracao_consumidor_sql_e_interface_semantica_iguais(uf, rede):
    dsn = os.environ.get("SEMANTICA_BANCADA_DSN") or os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("Defina SEMANTICA_BANCADA_DSN ou DATABASE_URL para integração")
    filtros = {"ano": 2024}
    if uf:
        filtros["uf"] = uf
    if rede:
        filtros["rede"] = rede
    via_python = semantica.consultar(dsn, "media_enem_escolas", filtros)["dados"]
    with psycopg.connect(dsn) as conn:
        via_sql = conn.execute(
            "select metricas.media_enem_escolas(%s::jsonb)",
            (json.dumps(filtros),),
        ).fetchone()[0]
    assert via_python == via_sql
