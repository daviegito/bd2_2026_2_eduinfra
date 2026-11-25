"""As três formas de guardar a camada analítica, atrás da mesma interface.

Só as comparações do ADR 0003 usam este módulo. O PyIceberg não é dependência do
projeto: entra com `uv run --with` e é importado apenas dentro da classe Iceberg.
"""

import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
from deltalake import DeltaTable

from eduinfra.lake.camada_analitica import PARTICOES, TABELA, arquivos_da_tabela, gravar

CHAVE = ("ano_censo", "co_entidade", "item_codigo")


@dataclass(frozen=True)
class Tamanho:
    arquivos: int
    bytes_em_disco: int
    arquivos_de_metadados: int


def padronizar(dados: pa.Table) -> pa.Table:
    # Iceberg não tem inteiro de 16 bits: os três formatos gravam o mesmo tipo.
    campos = [
        campo.with_type(pa.int32()) if pa.types.is_int16(campo.type) else campo
        for campo in dados.schema
    ]
    return dados.cast(pa.schema(campos))


def dividir_em_lotes(dados: pa.Table, lotes: int) -> list[pa.Table]:
    # Como em comparacao_particionamento: cada lote leva escolas inteiras e cai em todas as UFs.
    conexao = duckdb.connect()
    try:
        conexao.register("dados", dados)
        return [
            padronizar(conexao.execute("select * from dados where co_entidade % ? = ?", [lotes, lote]).to_arrow_table())
            for lote in range(lotes)
        ]
    finally:
        conexao.close()


class ParquetPuro:
    """Opção nula na sua melhor forma: grava à parte e renomeia, sem log de transações."""

    nome = "A. Parquet puro"

    def __init__(self, raiz: Path):
        self.raiz = raiz

    def iniciar(self, esquema: pa.Schema) -> None:
        self.raiz.mkdir(parents=True)

    def acrescentar(self, lote: pa.Table) -> None:
        temporaria = self.raiz.with_name(f"{self.raiz.name}_{uuid.uuid4().hex}")
        _copiar_particionado(lote, temporaria)
        # Cada rename é atômico; o conjunto das 27 pastas não é.
        for arquivo in temporaria.rglob("*.parquet"):
            destino = self.raiz / arquivo.relative_to(temporaria)
            destino.parent.mkdir(parents=True, exist_ok=True)
            arquivo.rename(destino)
        shutil.rmtree(temporaria)

    def reaplicar(self, lote: pa.Table) -> None:
        faltantes = _itens_que_faltam(self, lote)
        if faltantes.num_rows:
            self.acrescentar(faltantes)

    def compactar(self) -> None:
        nova = self.raiz.with_name(f"{self.raiz.name}_nova")
        velha = self.raiz.with_name(f"{self.raiz.name}_velha")
        conexao = duckdb.connect()
        try:
            conexao.execute(
                f"""
                copy (select * from {self.origem_de_leitura()}) to '{nova.as_posix()}'
                (format parquet, compression zstd, partition_by ({', '.join(PARTICOES)}))
                """
            )
        finally:
            conexao.close()
        # Entre os dois renames a pasta não existe: é o intervalo que o leitor concorrente encontra.
        self.raiz.rename(velha)
        nova.rename(self.raiz)
        shutil.rmtree(velha)

    def preparar(self, conexao: duckdb.DuckDBPyConnection) -> None:
        pass

    def origem_de_leitura(self) -> str:
        return _leitura_parquet(self.raiz)

    def tamanho(self) -> Tamanho:
        arquivos = list(self.raiz.rglob("*.parquet"))
        return Tamanho(len(arquivos), sum(arquivo.stat().st_size for arquivo in arquivos), 0)

    def leituras_da_copia(self, destino: Path) -> dict[str, str]:
        return {"padrão": _leitura_parquet(destino)}


class ParquetPuroDireto(ParquetPuro):
    """O caminho mais simples, que o DuckDB oferece: grava já na pasta da tabela."""

    nome = "A'. Parquet puro, gravação direta"

    def acrescentar(self, lote: pa.Table) -> None:
        _copiar_particionado(lote, self.raiz)


class Delta:
    nome = "B. Delta"

    def __init__(self, raiz: Path):
        self.raiz = raiz

    def iniciar(self, esquema: pa.Schema) -> None:
        pass

    def acrescentar(self, lote: pa.Table) -> None:
        gravar(self.raiz, lote, PARTICOES, modo="append")

    def reaplicar(self, lote: pa.Table) -> None:
        predicado = " and ".join(f"destino.{coluna} = lote.{coluna}" for coluna in CHAVE)
        DeltaTable(str(self.raiz)).merge(
            lote, predicate=predicado, source_alias="lote", target_alias="destino"
        ).when_not_matched_insert_all().execute()

    def compactar(self) -> None:
        tabela = DeltaTable(str(self.raiz))
        tabela.optimize.compact()
        # Retenção zero é o pior caso para o leitor concorrente: apaga os arquivos antigos na hora.
        tabela.vacuum(retention_hours=0, enforce_retention_duration=False, dry_run=False)
        tabela.create_checkpoint()

    def preparar(self, conexao: duckdb.DuckDBPyConnection) -> None:
        _carregar_extensao(conexao, "delta")

    def origem_de_leitura(self) -> str:
        return f"delta_scan('{self.raiz.as_posix()}')"

    def tamanho(self) -> Tamanho:
        dados = arquivos_da_tabela(DeltaTable(str(self.raiz)))
        log = [arquivo for arquivo in (self.raiz / "_delta_log").rglob("*") if arquivo.is_file()]
        return Tamanho(len(dados), sum(arquivo.stat().st_size for arquivo in dados), len(log))

    def leituras_da_copia(self, destino: Path) -> dict[str, str]:
        return {"padrão": f"delta_scan('{destino.as_posix()}')"}


class Iceberg:
    nome = "C. Iceberg"

    def __init__(self, raiz: Path):
        self.raiz = raiz

    def iniciar(self, esquema: pa.Schema) -> None:
        self.raiz.mkdir(parents=True)
        catalogo = self._catalogo()
        catalogo.create_namespace("eduinfra")
        tabela = catalogo.create_table(
            f"eduinfra.{TABELA}",
            schema=esquema,
            properties={"write.parquet.compression-codec": "zstd"},
        )
        from pyiceberg.transforms import IdentityTransform

        with tabela.update_spec() as especificacao:
            for coluna in PARTICOES:
                especificacao.add_field(coluna, IdentityTransform(), coluna)

    def acrescentar(self, lote: pa.Table) -> None:
        self._tabela().append(lote)

    def reaplicar(self, lote: pa.Table) -> None:
        # Mesmo caminho do Parquet puro, porque o upsert do PyIceberg não escala (medir_upsert).
        faltantes = _itens_que_faltam(self, lote)
        if faltantes.num_rows:
            self.acrescentar(faltantes)

    def upsert(self, lote: pa.Table) -> None:
        self._tabela().upsert(lote, join_cols=list(CHAVE), when_matched_update_all=False, when_not_matched_insert_all=True)

    def compactar(self) -> None:
        # O PyIceberg 0.12 não reescreve arquivos de dados: só resta ler tudo e sobrescrever.
        tabela = self._tabela()
        tabela.overwrite(tabela.scan().to_arrow())
        tabela.maintenance.expire_snapshots().older_than(datetime.now(timezone.utc)).commit()

    def preparar(self, conexao: duckdb.DuckDBPyConnection) -> None:
        _carregar_extensao(conexao, "iceberg")
        conexao.execute("set unsafe_enable_version_guessing = true")

    def origem_de_leitura(self) -> str:
        return f"iceberg_scan('{_caminho_local(self._tabela().metadata_location)}')"

    def tamanho(self) -> Tamanho:
        tabela = self._tabela()
        dados = list(tabela.scan().plan_files())
        metadados = [arquivo for arquivo in self.raiz.rglob("metadata/*") if arquivo.is_file()]
        return Tamanho(len(dados), sum(tarefa.file.file_size_in_bytes for tarefa in dados), len(metadados))

    def leituras_da_copia(self, destino: Path) -> dict[str, str]:
        relativo = Path(_caminho_local(self._tabela().metadata_location)).relative_to(self.raiz)
        pasta_da_tabela = destino / "eduinfra" / TABELA
        return {
            "padrão": f"iceberg_scan('{(destino / relativo).as_posix()}')",
            "allow_moved_paths e version guessing": (
                f"iceberg_scan('{pasta_da_tabela.as_posix()}', allow_moved_paths = true, version = '?')"
            ),
        }

    def _catalogo(self):
        from pyiceberg.catalog.sql import SqlCatalog

        return SqlCatalog(
            "local",
            uri=f"sqlite:///{(self.raiz / 'catalogo.db').as_posix()}",
            warehouse=self.raiz.as_uri(),
        )

    def _tabela(self):
        return self._catalogo().load_table(f"eduinfra.{TABELA}")


ALTERNATIVAS = (ParquetPuro, Delta, Iceberg)


def _copiar_particionado(lote: pa.Table, pasta: Path) -> None:
    conexao = duckdb.connect()
    try:
        conexao.register("lote", lote)
        conexao.execute(
            f"""
            copy lote to '{pasta.as_posix()}'
            (format parquet, compression zstd, partition_by ({', '.join(PARTICOES)}),
             overwrite_or_ignore, filename_pattern 'parte_{uuid.uuid4().hex}_{{i}}')
            """
        )
    finally:
        conexao.close()


def _carregar_extensao(conexao: duckdb.DuckDBPyConnection, extensao: str) -> None:
    try:
        conexao.execute(f"load {extensao}")
    except duckdb.Error:
        conexao.execute(f"install {extensao}")
        conexao.execute(f"load {extensao}")


def _itens_que_faltam(formato, lote: pa.Table) -> pa.Table:
    # A origem é insert-only: reaplicar um lote é acrescentar o que falta, sem atualizar nada.
    chave = ", ".join(CHAVE)
    conexao = duckdb.connect()
    try:
        formato.preparar(conexao)
        conexao.register("lote", lote)
        return padronizar(
            conexao.execute(
                f"select l.* from lote l anti join (select {chave} from {formato.origem_de_leitura()}) t using ({chave})"
            ).to_arrow_table()
        )
    finally:
        conexao.close()


def _leitura_parquet(raiz: Path) -> str:
    return f"read_parquet('{raiz.as_posix()}/**/*.parquet', hive_partitioning = true)"


def _caminho_local(uri: str) -> str:
    return uri.removeprefix("file://")
