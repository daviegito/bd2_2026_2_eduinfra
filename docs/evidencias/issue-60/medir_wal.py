"""Mede a Gold com consumidor ativo; use somente banco/arquivos de desenvolvimento."""

import argparse
import json
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pyarrow.dataset as ds
from deltalake import DeltaTable

from eduinfra import cdc, eventos, fluxo_cdc, modelo_analitico
from eduinfra.configuracao import carregar_configuracao
from eduinfra.vistorias_de_teste import inserir_vistoria


def medir(opcoes):
    configuracao = carregar_configuracao()
    fluxo_cdc.publicar_linha_de_base(configuracao)
    parar = threading.Event()
    pronto = threading.Event()
    erros = []
    passadas = []
    amostras = []

    def consumir():
        try:
            while not parar.is_set():
                passadas.append(fluxo_cdc.passar_uma_vez(configuracao))
                pronto.set()
                parar.wait(opcoes.intervalo)
        except Exception as erro:
            erros.append(str(erro))
            pronto.set()

    def monitorar():
        try:
            with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
                ultimo_checkpoint = time.monotonic()
                while not parar.is_set():
                    estado = cdc.estado_do_slot(conexao)
                    amostras.append({"segundos": round(time.monotonic() - inicio, 3),
                                     "wal_retido_bytes": estado.wal_retido_em_bytes,
                                     "atraso_bytes": estado.atraso_em_bytes,
                                     "wal_status": estado.situacao_do_wal})
                    if time.monotonic() - ultimo_checkpoint >= opcoes.checkpoint_segundos:
                        conexao.execute("checkpoint")
                        ultimo_checkpoint = time.monotonic()
                    parar.wait(0.2)
        except Exception as erro:
            erros.append(str(erro))

    consumidor = threading.Thread(target=consumir, daemon=True)
    consumidor.start()
    if not pronto.wait(120) or erros:
        parar.set()
        consumidor.join(timeout=120)
        raise RuntimeError("consumidor não iniciou: " + str(erros))
    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        limite = conexao.execute("show max_slot_wal_keep_size").fetchone()[0]
        inicio_lsn = cdc.posicao_persistida(conexao)
        escolas = conexao.execute("select count(*) from escola").fetchone()[0]
        itens = conexao.execute("select count(*) from vistoria_item").fetchone()[0]
    inicio = time.monotonic()
    monitor = threading.Thread(target=monitorar, daemon=True)
    monitor.start()
    falha_build = None

    def construir():
        nonlocal falha_build
        try:
            if opcoes.comando:
                subprocess.run(opcoes.comando, check=True)
            else:
                modelo_analitico.construir(configuracao.dsn)
        except Exception as erro:
            falha_build = str(erro)

    construtor = threading.Thread(target=construir, daemon=True)
    construtor.start()
    time.sleep(1)
    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        marcador = inserir_vistoria(conexao, "TESTE-ISSUE60", "Teste de WAL durante construção da Gold", datetime.now(timezone.utc) - timedelta(hours=1))
        por_vistoria = conexao.execute("select count(*) from item_infraestrutura").fetchone()[0]
    construtor.join(timeout=600)
    duracao = time.monotonic() - inicio
    try:
        if construtor.is_alive():
            raise RuntimeError("construção ultrapassou 600 s")
        if falha_build:
            erros.append(falha_build)
        # Exercita o limite de retenção e dá ao consumidor oportunidade de liberar o WAL.
        with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
            conexao.execute("checkpoint")
        prazo = time.monotonic() + 60
        entregues = 0
        while time.monotonic() < prazo and not erros:
            tabela = DeltaTable(str(eventos.caminho_da_tabela(configuracao)))
            dados = tabela.to_pyarrow_dataset().to_table(filter=ds.field("vistoria_id") == marcador)
            entregues = dados.num_rows
            if entregues == por_vistoria and len(passadas) >= 3:
                break
            time.sleep(0.2)
        time.sleep(opcoes.intervalo * 2)
    finally:
        parar.set()
        consumidor.join(timeout=120)
        monitor.join(timeout=120)
    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        final = cdc.estado_do_slot(conexao)
        fim_lsn = cdc.posicao_persistida(conexao)
    tabela = DeltaTable(str(eventos.caminho_da_tabela(configuracao)))
    dados = tabela.to_pyarrow_dataset().to_table(filter=ds.field("vistoria_id") == marcador)
    chaves = set(zip(dados.column("vistoria_id").to_pylist(), dados.column("item_codigo").to_pylist()))
    resultado = {
        "data_utc": datetime.now(timezone.utc).isoformat(), "comando": opcoes.comando or ["construir-gold (SQL)"],
        "escolas": escolas, "itens_antes": itens, "limite_slot": limite,
        "intervalo_consumidor_s": opcoes.intervalo, "checkpoint_s": opcoes.checkpoint_segundos,
        "duracao_construcao_e_insercao_s": round(duracao, 3), "wal_gerado_bytes": fim_lsn - inicio_lsn,
        "pico_retido_bytes": max(a["wal_retido_bytes"] for a in amostras),
        "retido_final_bytes": final.wal_retido_em_bytes, "wal_status_final": final.situacao_do_wal,
        "estados_observados": sorted({a["wal_status"] for a in amostras}),
        "passadas": len(passadas), "itens_do_marcador": dados.num_rows, "chaves_do_marcador": len(chaves),
        "itens_esperados_do_marcador": por_vistoria, "erros": erros,
        "amostras": amostras,
    }
    opcoes.saida.parent.mkdir(parents=True, exist_ok=True)
    opcoes.saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k:v for k,v in resultado.items() if k != "amostras"}, ensure_ascii=False, indent=2))
    if erros or final.situacao_do_wal in {"lost", "unreserved"} or dados.num_rows != por_vistoria or len(chaves) != por_vistoria:
        raise RuntimeError("medição encontrou falha; consulte a saída antes de usar a configuração")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmar-banco-de-teste", action="store_true", required=True)
    parser.add_argument("--saida", type=Path, required=True)
    parser.add_argument("--intervalo", type=float, default=2)
    parser.add_argument("--checkpoint-segundos", type=float, default=10)
    parser.add_argument("--comando", nargs=argparse.REMAINDER)
    argumentos = parser.parse_args()
    if argumentos.intervalo <= 0 or argumentos.checkpoint_segundos <= 0:
        parser.error("intervalo e checkpoint devem ser positivos")
    medir(argumentos)
