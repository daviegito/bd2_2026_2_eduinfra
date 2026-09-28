import random
from pathlib import Path
from typing import Iterable

from eduinfra.configuracao import Configuracao


def _random_name():
    return "Escola " + str(random.randint(1, 10_000_000))


def gerar_amostra(config: Configuracao, n_escolas: int = 1000) -> None:
    import psycopg
    import datetime

    dsn = config.dsn
    # conecta e insere dados essenciais
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            # garantir catálogo de itens; se vazio, popular com os itens padrão
            itens = [row[0] for row in conn.execute("select codigo from item_infraestrutura").fetchall()]
            if not itens:
                padrao = [
                    ("biblioteca", "Biblioteca", "IN_BIBLIOTECA", "pedagogico"),
                    ("sala_leitura", "Sala de leitura", "IN_SALA_LEITURA", "pedagogico"),
                    ("lab_ciencias", "Laboratório de ciências", "IN_LABORATORIO_CIENCIAS", "pedagogico"),
                    ("lab_informatica", "Laboratório de informática", "IN_LABORATORIO_INFORMATICA", "pedagogico"),
                    ("quadra_esportes", "Quadra de esportes", "IN_QUADRA_ESPORTES", "esportivo"),
                ]
                for codigo, desc, coluna, categoria in padrao:
                    conn.execute(
                        "insert into item_infraestrutura (codigo, descricao, coluna_censo, categoria) values (%s,%s,%s,%s) on conflict (codigo) do nothing",
                        (codigo, desc, coluna, categoria),
                    )
                itens = [row[0] for row in conn.execute("select codigo from item_infraestrutura").fetchall()]

            # gerar municipios
            for i in range(1, max(10, n_escolas // 100) + 1):
                conn.execute(
                    "insert into municipio (co_municipio, no_municipio, sg_uf, co_uf) values (%s,%s,%s,%s) on conflict (co_municipio) do nothing",
                    (i, f"Mun{i}", "DF", 53),
                )

            # gerar escolas
            for e in range(1, n_escolas + 1):
                co_entidade = 100000 + e
                conn.execute(
                    "insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia) values (%s,%s,%s,%s,%s,%s) on conflict (co_entidade) do nothing",
                    (co_entidade, _random_name(), (e % (n_escolas // 100 + 1)) + 1, random.randint(1, 4), random.randint(1, 2), 2024),
                )

            # gerar vistorias e itens (uma vistoria por escola, com N itens aleatórios)
            tecnico = "SAMPLE-GEN"
            conn.execute(
                "insert into tecnico (matricula, nome, lotacao_uf) values (%s,%s,%s) on conflict (matricula) do nothing",
                (tecnico, "Gerador de amostra", "DF"),
            )

            resultado = conn.execute("select id from tecnico where matricula = %s", (tecnico,)).fetchone()
            tecnico_id = resultado[0]

            now = datetime.datetime.utcnow()
            for e in range(1, n_escolas + 1):
                co_entidade = 100000 + e
                conn.execute(
                    "insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em, registrado_em) values (%s,%s,%s,%s,%s) on conflict (co_entidade, ocorrido_em) where origem = 'sintetico' do nothing",
                    (co_entidade, tecnico_id, "sintetico", now, now),
                )

                # inserir alguns itens escolhidos a partir do catálogo real
                sample_items = random.sample(itens, min(len(itens), 5))
                for codigo in sample_items:
                    situacao = random.choice(["existente", "inexistente", "nao_informado"])
                    conn.execute(
                        "insert into vistoria_item (vistoria_id, item_codigo, situacao, quantidade) select v.id, %s, %s, %s from vistoria v where v.co_entidade = %s and v.ocorrido_em = %s on conflict (vistoria_id, item_codigo) do nothing",
                        (codigo, situacao, random.randint(0, 10), co_entidade, now),
                    )

    print(f"Amostra gerada: {n_escolas} escolas")
