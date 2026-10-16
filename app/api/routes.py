"""Rotas HTTP da API."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Alert, Comic
from app.schemas import ComicCreate, ComicUpdate, RefreshRequest, SearchRequest
from app.scrapers import available_sources
from app.services.banca_service import (
    add_comic,
    banca_payload,
    refresh_all,
    refresh_comic,
    seed_demo,
    stats,
)
from app.services.price_service import (
    alert_to_dict,
    comic_alerts,
    comic_history,
    comic_to_dict,
    entry_to_dict,
    mark_alerts_seen,
    offer_to_dict,
    recent_changes,
)
from app.services.search_service import smart_search
from app.services.state_service import load_state, open_state_session, run_action
from app.services.text import normalize
from app.services.transfer_service import export_payload, import_payload

router = APIRouter(prefix="/api", tags=["banca"])


# ---------------------------------------------------------------- utilitarios
def _get_comic_or_404(db: Session, comic_id: int) -> Comic:
    comic = db.get(Comic, comic_id)
    if comic is None:
        raise HTTPException(status_code=404, detail="Quadrinho nao encontrado na banca")
    return comic


# ----------------------------------------------------------------- metadados
@router.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": settings.version}


@router.get("/sources")
def list_sources() -> list[dict]:
    """Fontes de preco conhecidas e quais estao ativas."""
    return available_sources(settings)


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)) -> dict:
    return stats(db)


@router.get("/changes")
def get_recent_changes(
    hours: int = Query(default=48, ge=1, le=24 * 30),
    limit: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Ultimas variacoes de preco registradas (quedas e altas)."""
    return recent_changes(db, hours=hours, limit=limit)


# --------------------------------------------------------------------- avisos
@router.get("/alerts")
def list_alerts(
    only_unseen: bool = Query(default=False, description="Somente avisos nao lidos"),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """Avisos gerados pela monitoracao (ex.: produto voltou ao estoque)."""
    alerts = comic_alerts(db, only_unseen=only_unseen, limit=limit)
    unseen = (
        db.execute(select(func.count(Alert.id)).where(Alert.seen.is_(False))).scalar() or 0
    )
    return {"unseen": int(unseen), "alerts": [alert_to_dict(alert) for alert in alerts]}


@router.post("/alerts/ack")
def ack_alerts(
    payload: dict | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Marca um aviso (``alert_id``) ou todos como lidos."""
    alert_id = None
    if payload and payload.get("alert_id") is not None:
        alert_id = int(payload["alert_id"])
    marked = mark_alerts_seen(db, alert_id=alert_id)
    db.commit()
    return {"marked": marked}


# --------------------------------------------------------------------- busca
@router.post("/search")
def search(payload: SearchRequest) -> dict:
    """Busca inteligente: agrega lojas, agrupa produtos e aponta o menor preco.

    O estado da prateleira (enviado pelo navegador) serve apenas para marcar
    quais resultados ja estao na banca. O servidor nao guarda nada.
    """
    if payload.state:
        db = open_state_session()
        try:
            load_state(db, payload.state)
            return smart_search(db, payload.query, sources=payload.sources, limit=payload.limit)
        finally:
            db.close()

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        return smart_search(db, payload.query, sources=payload.sources, limit=payload.limit)
    finally:
        db.close()


# ------------------------------------------------------- motor stateless (banca)
@router.post("/state")
def state_endpoint(payload: dict = Body(...)) -> dict:
    """Recebe a prateleira do navegador, aplica a acao e devolve o estado novo.

    O servidor nao persiste nada: guarda a prateleira e responsabilidade do
    navegador. Aqui so acontecem scraping e calculos.
    """
    try:
        return run_action(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------- banca
@router.get("/comics")
def list_comics(
    q: str | None = Query(default=None, description="Filtra por titulo"),
    only_lowest: bool = Query(default=False, description="Somente no menor preco historico"),
    db: Session = Depends(get_db),
) -> list[dict]:
    payload = banca_payload(db)
    if q:
        needle = normalize(q)
        payload = [item for item in payload if needle in item["normalized_title"]]
    if only_lowest:
        payload = [item for item in payload if item["snapshot"]["is_lowest_ever"]]
    return payload


@router.post("/comics", status_code=status.HTTP_201_CREATED)
def create_comic_endpoint(payload: ComicCreate, db: Session = Depends(get_db)) -> dict:
    """Adiciona um quadrinho a banca e ja coleta os precos iniciais."""
    try:
        comic, created = add_comic(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "created": created,
        "detail": "Quadrinho adicionado a banca" if created else "Quadrinho ja estava na banca",
        "comic": comic_to_dict(db, comic, history_points=30),
    }


@router.get("/comics/{comic_id}")
def get_comic(
    comic_id: int,
    history_limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    comic = _get_comic_or_404(db, comic_id)
    payload = comic_to_dict(db, comic, history_points=200)
    payload["history"] = [
        entry_to_dict(entry)
        for entry in comic_history(db, comic.id, only_consolidated=True, limit=history_limit)
    ]
    payload["source_history"] = [
        entry_to_dict(entry) for entry in comic_history(db, comic.id, limit=history_limit)
    ]
    payload["history_sources"] = sorted(
        {offer.source for offer in comic.offers}
        | {entry.source for entry in comic.price_history if entry.source}
    )
    return payload


@router.patch("/comics/{comic_id}")
def update_comic(
    comic_id: int,
    payload: ComicUpdate = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    comic = _get_comic_or_404(db, comic_id)
    data = payload.model_dump(exclude_unset=True)

    if "title" in data and data["title"]:
        comic.title = data["title"].strip()
        comic.normalized_title = normalize(comic.title)

    for field in ("publisher", "issue", "volume", "description", "cover_url", "tags"):
        if field in data:
            setattr(comic, field, data[field])
    for field in ("reference_price", "target_price"):
        if field in data:
            setattr(comic, field, data[field])
    if "tracked" in data and data["tracked"] is not None:
        comic.tracked = data["tracked"]

    db.commit()
    db.refresh(comic)
    return comic_to_dict(db, comic, history_points=30)


@router.delete("/comics/{comic_id}")
def delete_comic(comic_id: int, db: Session = Depends(get_db)) -> dict:
    comic = _get_comic_or_404(db, comic_id)
    title = comic.title
    db.delete(comic)
    db.commit()
    return {"detail": f"'{title}' removido da banca"}


@router.post("/comics/{comic_id}/refresh")
def refresh_one(
    comic_id: int,
    payload: RefreshRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Reconsulta as lojas e registra as variacoes de preco encontradas."""
    comic = _get_comic_or_404(db, comic_id)
    sources = payload.sources if payload else None
    try:
        result = refresh_comic(db, comic, sources=sources)
    except Exception as exc:  # noqa: BLE001 - devolve erro legivel para a UI
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Falha ao atualizar precos: {exc}") from exc
    return {
        "detail": f"Precos atualizados ({result['history_entries']} mudanca(s) registrada(s))",
        "result": result,
        "comic": comic_to_dict(db, comic, history_points=60),
    }


@router.get("/comics/{comic_id}/history")
def get_history(
    comic_id: int,
    source: str | None = Query(default=None, description="Loja especifica; vazio = consolidado"),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict:
    comic = _get_comic_or_404(db, comic_id)
    if source:
        entries = comic_history(db, comic.id, source=source, limit=limit)
    else:
        entries = comic_history(db, comic.id, only_consolidated=True, limit=limit)

    changes = [entry for entry in entries if entry.delta is not None]
    drops = [entry for entry in changes if (entry.delta or 0) < 0]
    return {
        "comic_id": comic.id,
        "title": comic.title,
        "source": source,
        "total": len(entries),
        "drops": len(drops),
        "rises": len([entry for entry in changes if (entry.delta or 0) > 0]),
        "biggest_drop": entry_to_dict(min(drops, key=lambda item: item.delta)) if drops else None,
        "entries": [entry_to_dict(entry) for entry in entries],
    }


@router.get("/comics/{comic_id}/history.csv")
def export_history(
    comic_id: int,
    source: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> Response:
    """Exporta o historico de precos em CSV (Excel-friendly)."""
    comic = _get_comic_or_404(db, comic_id)
    entries = (
        comic_history(db, comic.id, source=source, limit=2000)
        if source
        else comic_history(db, comic.id, only_consolidated=True, limit=2000)
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(
        [
            "data",
            "loja",
            "preco",
            "preco_anterior",
            "variacao",
            "variacao_pct",
            "desconto_pct",
            "menor_ate_aqui",
            "tipo",
        ]
    )
    for entry in reversed(entries):
        writer.writerow(
            [
                entry.recorded_at.strftime("%d/%m/%Y %H:%M"),
                entry.source or "melhor preco",
                f"{entry.price:.2f}".replace(".", ","),
                "" if entry.previous_price is None else f"{entry.previous_price:.2f}".replace(".", ","),
                "" if entry.delta is None else f"{entry.delta:.2f}".replace(".", ","),
                "" if entry.delta_pct is None else f"{entry.delta_pct:.2f}".replace(".", ","),
                "" if entry.discount_pct is None else f"{entry.discount_pct:.2f}".replace(".", ","),
                "" if entry.lowest_ever is None else f"{entry.lowest_ever:.2f}".replace(".", ","),
                entry.change_type,
            ]
        )

    filename = f"historico-{comic.id}-{normalize(comic.title) or 'quadrinho'}.csv"
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------- manutencao
@router.post("/refresh")
def refresh_everything(payload: RefreshRequest | None = Body(default=None)) -> dict:
    """Atualiza todos os quadrinhos monitorados."""
    from app.database import session_scope

    sources = payload.sources if payload else None
    with session_scope() as db:
        result = refresh_all(db, sources=sources)
    return {"detail": f"{result['comics']} quadrinho(s) atualizado(s)", "result": result}


@router.post("/seed")
def seed(db: Session = Depends(get_db)) -> dict:
    """Popula a banca com quadrinhos de demonstracao."""
    created = seed_demo(db)
    return {"detail": f"{created} quadrinho(s) adicionado(s) a banca", "created": created}


@router.get("/export")
def export_banca(db: Session = Depends(get_db)) -> dict:
    """Exporta a prateleira inteira (quadrinhos + historico) em JSON."""
    return export_payload(db)


@router.post("/import")
def import_banca(payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    """Importa um arquivo exportado, fazendo merge com a prateleira atual."""
    try:
        result = import_payload(db, payload)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "detail": (
            f"{result['comics_created']} novo(s), "
            f"{result['comics_updated']} atualizado(s), "
            f"{result['history_added']} registro(s) de historico"
        ),
        "result": result,
    }


@router.get("/comics/{comic_id}/offers")
def list_offers(comic_id: int, db: Session = Depends(get_db)) -> list[dict]:
    comic = _get_comic_or_404(db, comic_id)
    return [
        offer_to_dict(offer, comic.reference_price)
        for offer in sorted(
            comic.offers, key=lambda item: (item.price is None, item.price or 0)
        )
    ]


@router.get("/tracked")
def tracked_titles(db: Session = Depends(get_db)) -> list[str]:
    return list(db.execute(select(Comic.title).order_by(Comic.title)).scalars())
