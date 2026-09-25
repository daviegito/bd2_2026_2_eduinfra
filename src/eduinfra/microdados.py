import logging
import time
import zipfile
from pathlib import Path

import httpx

from eduinfra.configuracao import Configuracao
from eduinfra.tls import contexto_para

log = logging.getLogger(__name__)

NOME_ARQUIVO_ESCOLAS = "microdados_ed_basica_{ano}.csv"
TAMANHO_BLOCO = 1 << 22
TENTATIVAS_DE_DOWNLOAD = 6
ESPERA_INICIAL = 5


def garantir_microdados(configuracao: Configuracao) -> Path:
    destino = configuracao.diretorio_dados / "bruto"
    destino.mkdir(parents=True, exist_ok=True)

    csv_escolas = destino / NOME_ARQUIVO_ESCOLAS.format(ano=configuracao.ano_censo)
    if csv_escolas.exists():
        return csv_escolas

    pacote = destino / f"censo_escolar_{configuracao.ano_censo}.zip"
    if not pacote.exists():
        _baixar(configuracao.url_censo, pacote)

    return _extrair_csv_de_escolas(pacote, csv_escolas)


def _baixar(url: str, destino: Path) -> None:
    # O servidor do INEP derruba a transferência com frequência; sem retomada
    # por Range a carga vira loteria.
    parcial = destino.with_suffix(".parcial")

    for tentativa in range(1, TENTATIVAS_DE_DOWNLOAD + 1):
        try:
            _transferir(url, parcial)
            parcial.replace(destino)
            return
        except (httpx.HTTPError, OSError) as falha:
            if tentativa == TENTATIVAS_DE_DOWNLOAD:
                raise RuntimeError(
                    f"download do Censo falhou após {TENTATIVAS_DE_DOWNLOAD} tentativas: {falha}"
                ) from falha

            espera = ESPERA_INICIAL * 2 ** (tentativa - 1)
            log.warning("download interrompido (%s); nova tentativa em %ds", falha, espera)
            time.sleep(espera)


def _transferir(url: str, parcial: Path) -> None:
    baixado = parcial.stat().st_size if parcial.exists() else 0
    cabecalhos = {"Range": f"bytes={baixado}-"} if baixado else {}

    with httpx.stream(
        "GET",
        url,
        headers=cabecalhos,
        follow_redirects=True,
        timeout=120.0,
        verify=contexto_para(url),
    ) as resposta:
        if baixado and resposta.status_code == httpx.codes.OK:
            baixado = 0  # servidor ignorou o Range e vai reenviar tudo

        resposta.raise_for_status()
        log.info("baixando microdados do Censo Escolar a partir do byte %d", baixado)

        with parcial.open("ab" if baixado else "wb") as arquivo:
            for bloco in resposta.iter_bytes(TAMANHO_BLOCO):
                arquivo.write(bloco)


def _extrair_csv_de_escolas(pacote: Path, destino: Path) -> Path:
    # A pasta raiz muda entre republicações do mesmo ano; busca por sufixo.
    with zipfile.ZipFile(pacote) as compactado:
        candidatos = [
            nome for nome in compactado.namelist() if nome.endswith(destino.name)
        ]
        if not candidatos:
            raise RuntimeError(
                f"{destino.name} não foi encontrado dentro de {pacote.name}; "
                "confira se a URL do Censo aponta para o ano configurado"
            )

        with compactado.open(candidatos[0]) as origem, destino.open("wb") as saida:
            while bloco := origem.read(TAMANHO_BLOCO):
                saida.write(bloco)

    return destino
