"""Reconstrução da cadeia de certificados pela extensão AIA.

O servidor do INEP apresenta apenas o certificado folha. A busca pelo emissor
não relaxa a verificação: a âncora continua sendo uma raiz pública instalada.
Contexto completo no ADR 0002.
"""

import logging
import socket
import ssl
import time
from urllib.parse import urlsplit

import certifi
import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

log = logging.getLogger(__name__)

TEMPO_LIMITE = 30.0
TENTATIVAS_DE_INSPECAO = 4
ESPERA_ENTRE_INSPECOES = 3


def contexto_para(url: str) -> ssl.SSLContext:
    # A raiz do certificado do INEP está no pacote do sistema, não no certifi.
    contexto = ssl.create_default_context()
    contexto.load_verify_locations(cafile=certifi.where())

    host = urlsplit(url).hostname
    if not host:
        return contexto

    intermediario = _intermediario_por_aia(host)
    if intermediario:
        contexto.load_verify_locations(cadata=intermediario)

    return contexto


def _intermediario_por_aia(host: str, porta: int = 443) -> str | None:
    folha = _certificado_apresentado(host, porta)
    if folha is None:
        return None

    endereco = _endereco_do_emissor(folha)
    if not endereco:
        return None

    try:
        resposta = httpx.get(endereco, timeout=TEMPO_LIMITE)
        resposta.raise_for_status()
        emissor = x509.load_der_x509_certificate(resposta.content)
    except (httpx.HTTPError, ValueError) as falha:
        log.warning("não foi possível obter o certificado intermediário em %s: %s", endereco, falha)
        return None

    log.info("cadeia completada com o intermediário %s", emissor.subject.rfc4514_string())

    return emissor.public_bytes(Encoding.PEM).decode("ascii")


def _certificado_apresentado(host: str, porta: int) -> x509.Certificate | None:
    # Conexão sem verificação usada apenas para ler o certificado oferecido:
    # nada daqui vira confiança.
    contexto = ssl._create_unverified_context()  # noqa: S323

    for tentativa in range(1, TENTATIVAS_DE_INSPECAO + 1):
        try:
            with socket.create_connection((host, porta), timeout=TEMPO_LIMITE) as conexao:
                with contexto.wrap_socket(conexao, server_hostname=host) as tunel:
                    bruto = tunel.getpeercert(binary_form=True)

            return x509.load_der_x509_certificate(bruto) if bruto else None
        except OSError as falha:
            if tentativa == TENTATIVAS_DE_INSPECAO:
                log.warning("não foi possível inspecionar o certificado de %s: %s", host, falha)
                return None

            time.sleep(ESPERA_ENTRE_INSPECOES)

    return None


def _endereco_do_emissor(certificado: x509.Certificate) -> str | None:
    try:
        extensao = certificado.extensions.get_extension_for_class(
            x509.AuthorityInformationAccess
        ).value
    except x509.ExtensionNotFound:
        return None

    for descricao in extensao:
        if descricao.access_method == x509.oid.AuthorityInformationAccessOID.CA_ISSUERS:
            return descricao.access_location.value

    return None
