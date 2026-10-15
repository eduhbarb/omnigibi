"""Ponto de entrada serverless (Vercel).

A Vercel carrega este modulo como funcao Python e procura por um objeto ASGI
chamado ``app``. Reexportamos a aplicacao FastAPI do projeto, garantindo que a
raiz do repositorio esteja no ``sys.path`` (o pacote ``app`` vive la).
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.main import app  # noqa: E402  (precisa vir depois do sys.path)

__all__ = ["app"]
