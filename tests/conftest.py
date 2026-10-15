"""Configuracao dos testes: banco temporario e ambiente isolado."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Precisa acontecer ANTES de importar app.* (a config e lida na importacao).
_TMP = Path(tempfile.mkdtemp(prefix="banca-testes-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'testes.db').as_posix()}"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["ENABLED_SOURCES"] = "simulador"

import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app import models  # noqa: E402, F401


@pytest.fixture()
def db():
    """Sessao com schema limpo a cada teste."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    """Cliente HTTP da API (tabelas ja criadas pela fixture ``db``)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
