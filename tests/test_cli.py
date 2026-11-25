import pytest

from eduinfra import cli


def comandos_do_parser() -> set[str]:
    posicional = next(acao for acao in cli.montar_parser()._actions if acao.dest == "comando")
    return set(posicional.choices)


def test_todo_comando_aceito_pelo_parser_tem_quem_o_execute():
    assert comandos_do_parser() == set(cli.COMANDOS) | {"tudo"}


def test_nenhum_pacote_registra_o_mesmo_comando_que_outro():
    registros = [
        cli.origem.COMANDOS, cli.ingestao.COMANDOS, cli.cdc.COMANDOS, cli.lake.COMANDOS,
        cli.gold.COMANDOS, cli.experimentos.COMANDOS, cli.consumo.COMANDOS,
    ]
    assert sum(len(registro) for registro in registros) == len(cli.COMANDOS)


def test_reprocessar_janela_sem_mes_encerra_com_erro_de_uso(monkeypatch):
    monkeypatch.setattr(cli, "carregar_configuracao", lambda: None)
    with pytest.raises(SystemExit) as saida:
        cli.main(["reprocessar-janela"])
    assert saida.value.code == 2
