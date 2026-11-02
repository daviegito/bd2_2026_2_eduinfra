"""Regras puras de leitura do plano: não precisam de banco."""

from eduinfra.otimizacao_consulta import (
    Comparacao,
    Execucao,
    Medicao,
    erros_de_estimativa,
    formatar_markdown,
    resumir_execucao,
)


def no(tipo: str, estimadas: int, reais: float, *filhos: dict, **extras) -> dict:
    return {"Node Type": tipo, "Plan Rows": estimadas, "Actual Rows": reais, "Plans": list(filhos), **extras}


def plano_com_buffers(hit_raiz: int, hit_filho: int) -> list:
    filho = no("Seq Scan", 10, 10, **{"Shared Hit Blocks": hit_filho})
    raiz = no(
        "Aggregate",
        1,
        1,
        filho,
        **{
            "Shared Hit Blocks": hit_raiz,
            "Shared Read Blocks": 7,
            "Temp Read Blocks": 3,
            "Temp Written Blocks": 4,
            "I/O Read Time": 1.5,
        },
    )
    return [{"Plan": raiz, "Execution Time": 12.5}]


def test_buffers_vem_so_da_raiz_para_nao_contar_os_filhos_duas_vezes():
    execucao = resumir_execucao(plano_com_buffers(hit_raiz=100, hit_filho=100))

    assert execucao == Execucao(12.5, 1.5, 100, 7, 3, 4)


def test_execucao_sem_tempo_de_io_conta_zero():
    plano = plano_com_buffers(hit_raiz=1, hit_filho=1)
    del plano[0]["Plan"]["I/O Read Time"]

    assert resumir_execucao(plano).leitura_em_disco_ms == 0.0


def test_erro_de_estimativa_acha_o_no_interno_nos_dois_sentidos_e_ordena_pelo_maior():
    subestimado = no("Subquery Scan", 917, 110806, **{"Alias": "s"})
    superestimado = no("Seq Scan", 5000, 100, **{"Relation Name": "escola"})
    plano = [{"Plan": no("Nested Loop", 10, 10, subestimado, superestimado)}]

    erros = erros_de_estimativa(plano)

    assert [(erro.no, erro.linhas_estimadas, erro.linhas_reais) for erro in erros] == [
        ("Subquery Scan em s", 917, 110806),
        ("Seq Scan em escola", 5000, 100),
    ]


def test_estimativa_dentro_do_fator_nao_e_erro():
    assert erros_de_estimativa([{"Plan": no("Seq Scan", 100, 500)}]) == []


def test_no_sem_nenhuma_linha_real_nao_divide_por_zero():
    assert [erro.fator for erro in erros_de_estimativa([{"Plan": no("Seq Scan", 50, 0)}])] == [50.0]


def medicao(tempos: list[float], linhas: list[tuple]) -> Medicao:
    serie = [Execucao(tempo, 1.0, 10, 20, 30, 40) for tempo in tempos]
    return Medicao([], "", serie, [], linhas)


def test_markdown_compara_pela_mediana_e_mantem_todas_as_execucoes():
    comparacao = Comparacao(medicao([900.0, 100.0, 300.0], [(1, 2)]), medicao([50.0], [(1, 2)]), medicao([20.0], [(1, 3)]))

    texto = formatar_markdown(comparacao)

    assert "| Tempo de execução | 300,0 ms | 50,0 ms | 20,0 ms |" in texto
    assert "- antes: 900,0, 100,0, 300,0" in texto
    assert "A view e a Gold devolveram as mesmas linhas." in texto


def test_painel_com_outra_resposta_nao_invalida_a_comparacao():
    comparacao = Comparacao(medicao([1.0], [(1, 2)]), medicao([1.0], [(1, 2)]), medicao([1.0], [(1, 9)]))

    assert comparacao.mesmo_resultado


def test_markdown_avisa_quando_a_view_e_a_gold_devolvem_linhas_diferentes():
    comparacao = Comparacao(medicao([1.0], [(1, 2)]), medicao([1.0], [(1, 3)]), medicao([1.0], [(1, 3)]))

    assert "ATENÇÃO" in formatar_markdown(comparacao)
