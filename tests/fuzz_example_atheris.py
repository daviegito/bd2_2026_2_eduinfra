# Example Atheris fuzzing harness. Requires building and running with atheris.
import atheris
import sys

from eduinfra.transformacao import _validar_cabecalho
import duckdb


def TestOneInput(data: bytes) -> None:
    # Interpret input as a small CSV and run header validation path.
    try:
        texto = data.decode("latin-1", errors="ignore")
    except Exception:
        return

    # write to a temp file and call _validar_cabecalho against it
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        csv = Path(td) / "bruto.csv"
        csv.write_text(texto, encoding="latin-1")

        conexao = duckdb.connect()
        try:
            caminho = csv.as_posix().replace("'", "''")
            conexao.execute(
                f"create or replace view bruto as select * from read_csv('{caminho}', delim=';', header=true, encoding='latin-1', all_varchar=true, sample_size=-1)"
            )
            # call validation with an empty mapping
            try:
                _validar_cabecalho(conexao, {})
            except RuntimeError:
                # Expected for missing columns
                pass
        finally:
            conexao.close()


def main():
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
