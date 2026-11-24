"""Cache dos ZIPs preserva a extração real e não esconde entradas inválidas."""

from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import shutil
from threading import Thread
from zipfile import ZipFile

import pytest

from eduinfra import microdados
from eduinfra.configuracao import Configuracao
from transformacao.scripts import preparar_fontes_ci


def pacote(nome, conteudo):
    memoria = BytesIO()
    with ZipFile(memoria, "w") as arquivo:
        arquivo.writestr("DADOS/" + nome, conteudo)
    return memoria.getvalue()


@pytest.fixture
def servidor(monkeypatch):
    respostas = {
        "/censo.zip": pacote("microdados_ed_basica_2024.csv", b"CO_ENTIDADE;NO_ENTIDADE\n1;ESCOLA\n"),
        "/enem.zip": pacote("RESULTADOS_2024.csv", b"CO_ESCOLA;NU_NOTA_MT\n1;500\n"),
    }
    pedidos = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            pedidos.append(self.path)
            corpo = respostas[self.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *_):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    # O transporte HTTP continua real; o teste não precisa buscar certificados AIA.
    monkeypatch.setattr(microdados, "contexto_para", lambda _: True)
    try:
        yield f"http://127.0.0.1:{http.server_port}", respostas, pedidos
    finally:
        http.shutdown()
        thread.join()
        http.server_close()


def configuracao(pasta, url):
    return Configuracao(
        dsn="postgresql://nao-conectar", url_censo=url + "/censo.zip", ano_censo=2024,
        data_referencia=date(2024, 5, 29), diretorio_dados=pasta,
        arquivo_alembic=Path("nao-usar.ini"), url_enem=url + "/enem.zip", ano_enem=2024,
    )


def test_cache_restaurado_evita_download_e_extrai_os_mesmos_csv(tmp_path, servidor):
    url, _, pedidos = servidor
    frio = tmp_path / "frio"
    relatorio = preparar_fontes_ci.preparar(configuracao(frio, url), frio / "tempos.json")
    assert pedidos == ["/censo.zip", "/enem.zip"]
    assert [f["zip_reutilizado"] for f in relatorio["fontes"]] == [False, False]

    quente = tmp_path / "quente"
    for relativo in ("censo_escolar_2024.zip", "enem/microdados_enem_2024.zip"):
        destino = quente / "bruto" / relativo
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(frio / "bruto" / relativo, destino)
    segundo = preparar_fontes_ci.preparar(configuracao(quente, url), quente / "tempos.json")
    assert pedidos == ["/censo.zip", "/enem.zip"]
    assert [f["zip_reutilizado"] for f in segundo["fontes"]] == [True, True]
    assert (quente / "bruto/microdados_ed_basica_2024.csv").read_bytes() == b"CO_ENTIDADE;NO_ENTIDADE\n1;ESCOLA\n"
    assert (quente / "bruto/enem/RESULTADOS_2024.csv").read_bytes() == b"CO_ESCOLA;NU_NOTA_MT\n1;500\n"
    assert [f["sha256_zip"] for f in relatorio["fontes"]] == [f["sha256_zip"] for f in segundo["fontes"]]
    assert json.loads((quente / "tempos.json").read_text()) == segundo


def test_zip_sem_resultados_falha_e_nao_publica_relatorio_de_sucesso(tmp_path, servidor):
    url, respostas, _ = servidor
    respostas["/enem.zip"] = pacote("outro.csv", b"nao e RESULTADOS")
    with pytest.raises(RuntimeError, match="RESULTADOS_2024.csv não foi encontrado"):
        preparar_fontes_ci.preparar(configuracao(tmp_path, url), tmp_path / "tempos.json")
    assert not (tmp_path / "tempos.json").exists()
