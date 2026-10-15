"""Aplicacao FastAPI: API REST + interface web da banca."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import router as api_router
from app.config import settings
from app.database import init_db, session_scope
from app.services.banca_service import seed_demo
from app.services.scheduler import shutdown_scheduler, start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("banca")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    logger.info("Banco de dados pronto em %s", settings.database_url)

    if settings.seed_demo_data:
        with session_scope() as db:
            created = seed_demo(db)
        if created:
            logger.info("Banca inicial criada com %s quadrinhos de demonstracao", created)

    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()
        logger.info("Encerrando a banca...")


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description=(
        "API de monitoramento de precos de quadrinhos: busca agregada em varias "
        "lojas, deteccao do menor preco ja registrado e historico de variacoes."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request, exc: Exception) -> JSONResponse:  # pragma: no cover
    logger.exception("Erro nao tratado: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Erro interno do servidor"})


app.include_router(api_router)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(settings.web_dir / "index.html")


if settings.web_dir.exists():
    app.mount("/static", StaticFiles(directory=settings.web_dir), name="static")
