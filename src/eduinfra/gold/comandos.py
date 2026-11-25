import argparse
import logging
import sys
import unittest
from datetime import timedelta
from pathlib import Path

from eduinfra.gold import frescor
from eduinfra.nucleo.configuracao import Configuracao

log = logging.getLogger(__name__)

RAIZ_DO_REPOSITORIO = Path(__file__).resolve().parents[3]


def auditar_gold() -> bool:
    log.info("Iniciando auditoria E3 automática...")
    # Os testes de dados ficam em tests/, fora do pacote instalado: no contêiner a raiz é /app.
    for caminho in ("/app", str(Path.cwd()), str(RAIZ_DO_REPOSITORIO)):
        if caminho not in sys.path:
            sys.path.insert(0, caminho)

    suite = unittest.TestLoader().loadTestsFromName("tests.gold.test_e3_gold")
    resultado = unittest.TextTestRunner(verbosity=2).run(suite)

    if not resultado.wasSuccessful():
        log.error("A auditoria E3 FALHOU! Os dados da Gold estão inconsistentes.")
        return False

    log.info("Auditoria E3 concluída com sucesso!")
    return True


def medir_frescor(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    medicao = frescor.medir(configuracao.dsn, timedelta(hours=opcoes.sla_horas))
    print(frescor.resumo(medicao))
    return 0 if medicao.dentro_do_sla else 1


COMANDOS = {"medir-frescor": medir_frescor}
