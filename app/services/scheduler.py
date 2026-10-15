"""Atualizacao periodica dos precos em segundo plano."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import session_scope
from app.services.banca_service import refresh_all

logger = logging.getLogger("banca.scheduler")

_scheduler: BackgroundScheduler | None = None


def _job() -> None:
    logger.info("Iniciando atualizacao periodica de precos...")
    try:
        with session_scope() as db:
            result = refresh_all(db)
        logger.info(
            "Atualizacao concluida: %s quadrinhos, %s mudancas registradas",
            result["comics"],
            result["total_history_entries"],
        )
    except Exception:  # pragma: no cover - nunca derruba o servidor
        logger.exception("Falha na atualizacao periodica")


def start_scheduler(interval_minutes: int | None = None) -> BackgroundScheduler | None:
    """Liga o agendador (no-op quando desabilitado)."""
    global _scheduler
    if not settings.scheduler_enabled:
        logger.info("Agendador desativado (SCHEDULER_ENABLED=false)")
        return None
    if _scheduler is not None:
        return _scheduler

    minutes = interval_minutes or settings.scrape_interval_minutes
    scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
    scheduler.add_job(
        _job,
        trigger="interval",
        minutes=max(1, minutes),
        id="atualizar_precos",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Agendador ativo: atualizacao a cada %s minutos", minutes)
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
