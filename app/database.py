"""Configuracao do engine SQLAlchemy e sessao do banco de dados."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Base declarativa de todos os modelos."""


def _build_engine(url: str) -> Engine:
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        # SQLite + FastAPI (threads do ThreadPoolExecutor e do scheduler).
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


engine = _build_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """Dependencia FastAPI: entrega uma sessao e garante o fechamento."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Contexto transacional para uso fora do ciclo de requisicao (scheduler, scripts)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _drop_named_indexes(cursor, table: str) -> None:  # noqa: ANN001
    """Remove os indices nomeados de uma tabela renomeada (SQLite os mantem)."""
    rows = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ? "
        "AND name NOT LIKE 'sqlite_autoindex%'",
        (table,),
    ).fetchall()
    for (index_name,) in rows:
        cursor.execute(f'DROP INDEX IF EXISTS "{index_name}"')


def _migrate_legacy_sqlite() -> None:
    """Migra/conserta bancos SQLite antigos preservando os dados.

    Cobre dois cenarios:

    1. **schema antigo** — ``offers.price`` era ``NOT NULL`` ou faltavam colunas
       (``stock_changed_at``, ``preorder``). O SQLite nao altera a nulabilidade de
       uma coluna existente, entao a tabela e reconstruida;
    2. **FK pendurada** — uma versao anterior renomeava ``offers`` para
       ``offers_old`` sem desligar a reescrita de referencias do SQLite, o que
       reescreveu a FK de ``alerts`` para ``offers_old`` e a deixou apontando para
       uma tabela inexistente. Essas tabelas sao reconstruidas a partir do modelo.

    A reconstrucao renomeia a tabela para ``<nome>__old``, recria o schema atual e
    copia as linhas pelas colunas em comum, com ``foreign_keys`` desligado e
    ``legacy_alter_table`` ligado (para o rename nao mexer nas FKs de terceiros).
    """
    from app import models  # noqa: F401  (registra os mapeamentos)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "offers" not in tables:
        return

    offer_columns = {column["name"] for column in inspector.get_columns("offers")}
    price_not_null = any(
        column["name"] == "price" and not column["nullable"]
        for column in inspector.get_columns("offers")
    )
    offers_needs_rebuild = (
        price_not_null
        or "stock_changed_at" not in offer_columns
        or "preorder" not in offer_columns
    )

    # Tabelas cujo SQL ainda referencia "offers_old" (FK pendurada).
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name, sql FROM sqlite_master WHERE type = 'table'")
        ).fetchall()
    dangling: dict[str, set[str]] = {}
    for name, sql in rows:
        if name == "offers_old" or not sql or "offers_old" not in sql:
            continue
        dangling[name] = {column["name"] for column in inspector.get_columns(name)}

    if not offers_needs_rebuild and not dangling:
        return

    # 1) renomeia as tabelas que serao recriadas
    raw = engine.raw_connection()
    try:
        raw.commit()  # garante que as pragmas abaixo valham (nao ha transacao aberta)
        cursor = raw.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("PRAGMA legacy_alter_table=ON")
        for name in dangling:
            cursor.execute(f'ALTER TABLE "{name}" RENAME TO "{name}__old"')
            _drop_named_indexes(cursor, f"{name}__old")
        if offers_needs_rebuild:
            cursor.execute("ALTER TABLE offers RENAME TO offers_old")
            _drop_named_indexes(cursor, "offers_old")
        raw.commit()
        cursor.execute("PRAGMA legacy_alter_table=OFF")
        cursor.execute("PRAGMA foreign_keys=ON")
        raw.commit()
    finally:
        raw.close()

    # 2) recria o schema atual (offers e/ou alerts)
    Base.metadata.create_all(bind=engine)

    # 3) copia os dados e descarta as tabelas antigas
    raw = engine.raw_connection()
    try:
        raw.commit()  # pragmas so valem fora de transacao; o INSERT abaixo abre uma
        cursor = raw.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("PRAGMA legacy_alter_table=ON")
        if offers_needs_rebuild:
            common = [
                column.name
                for column in models.Offer.__table__.columns
                if column.name in offer_columns
            ]
            column_list = ", ".join(common)
            cursor.execute(
                f"INSERT INTO offers ({column_list}) SELECT {column_list} FROM offers_old"
            )
            cursor.execute("DROP TABLE offers_old")
        for name, old_columns in dangling.items():
            table = Base.metadata.tables.get(name)
            common = (
                [column.name for column in table.columns if column.name in old_columns]
                if table is not None
                else []
            )
            if common:
                column_list = ", ".join(common)
                cursor.execute(
                    f'INSERT INTO "{name}" ({column_list}) '
                    f'SELECT {column_list} FROM "{name}__old"'
                )
            cursor.execute(f'DROP TABLE "{name}__old"')
        raw.commit()  # encerra a transacao implicita do INSERT/DROP
        cursor.execute("PRAGMA legacy_alter_table=OFF")
        cursor.execute("PRAGMA foreign_keys=ON")
        raw.commit()
    finally:
        raw.close()


def init_db() -> None:
    """Cria as tabelas caso ainda nao existam e migra bancos antigos."""
    from app import models  # noqa: F401  (registra os mapeamentos)

    if settings.database_url.startswith("sqlite"):
        _migrate_legacy_sqlite()
    Base.metadata.create_all(bind=engine)
