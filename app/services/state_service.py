"""Motor stateless da banca: o estado vive no navegador do usuario.

O navegador envia a prateleira inteira (quadrinhos + historico + avisos) em cada
requisicao. O servidor monta uma base SQLite **em memoria**, aplica a acao pedida
(adicionar, atualizar precos, remover, ...) com a mesma logica de sempre e devolve
o estado recalculado para o navegador guardar.

O servidor fica apenas como motor de scraping e calculo. Isso resolve a Vercel:
o disco das funcoes e efemero e varias instancias atendem em paralelo, entao
guardar a prateleira la fazia os quadrinhos "sumirem" e "voltarem" a cada recarga.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, settings as default_settings
from app.database import Base
from app.models import Alert, Comic, utcnow
from app.services.banca_service import add_comic, banca_payload, refresh_comic, stats
from app.services.price_service import (
    alert_to_dict,
    comic_alerts,
    comic_history,
    comic_to_dict,
    entry_to_dict,
    find_comic,
    mark_alerts_seen,
    recent_changes,
)
from app.services.text import normalize
from app.services.transfer_service import (
    FORMAT_ALIASES,
    FORMAT_ID,
    export_payload,
    import_payload,
)

PATCHABLE_FIELDS = ("publisher", "issue", "volume", "description", "cover_url", "tags")
PATCHABLE_FLOATS = ("reference_price", "target_price")


# ------------------------------------------------------------------ utilitarios
def open_state_session() -> Session:
    """Abre uma base SQLite vazia em memoria para atender uma requisicao."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )
    return factory()


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_export(data: Any) -> dict[str, Any]:
    """Garante o envelope esperado por ``import_payload``."""
    if not isinstance(data, dict):
        raise ValueError("arquivo invalido: esperado um objeto JSON")
    out = dict(data)
    if out.get("format") not in FORMAT_ALIASES:
        out["format"] = FORMAT_ID
    return out


# -------------------------------------------------------------- carga do estado
def _load_alerts(db: Session, raw_alerts: Any) -> None:
    """Recria os avisos do navegador na base em memoria."""
    if not isinstance(raw_alerts, list):
        return
    for raw in raw_alerts:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("comic_title") or "").strip()
        comic = find_comic(db, title) if title else None
        if comic is None:
            continue
        db.add(
            Alert(
                comic_id=comic.id,
                kind=raw.get("kind") or "volta_estoque",
                message=raw.get("message") or "",
                source=raw.get("source"),
                source_label=raw.get("source_label"),
                seller=raw.get("seller"),
                price=_coerce_float(raw.get("price")),
                url=raw.get("url"),
                seen=bool(raw.get("seen")),
                created_at=_parse_dt(raw.get("created_at")) or utcnow(),
            )
        )
    db.commit()


def load_state(db: Session, state: Any) -> None:
    """Carrega o estado vindo do navegador (formato do export) na base em memoria."""
    if not isinstance(state, dict):
        return
    data = _normalize_export(state)
    data.pop("preferences", None)
    import_payload(db, data)
    _load_alerts(db, state.get("alerts"))


# --------------------------------------------------------------------- consulta
def _find(db: Session, key: Any) -> Comic | None:
    if not key:
        return None
    key = str(key)
    comic = db.execute(
        select(Comic).where(Comic.normalized_title == key)
    ).scalar_one_or_none()
    if comic is None:
        comic = find_comic(db, key)
    return comic


def _detail(db: Session, comic: Comic, *, history_limit: int = 300) -> dict[str, Any]:
    payload = comic_to_dict(db, comic, history_points=200)
    payload["key"] = comic.normalized_title
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


def _view(db: Session, *, sparkline_points: int = 20) -> dict[str, Any]:
    """Estado calculado que o navegador desenha e guarda."""
    comics = banca_payload(db, sparkline_points=sparkline_points)
    for item in comics:
        item["key"] = item.get("normalized_title")
    unseen = (
        db.execute(select(func.count(Alert.id)).where(Alert.seen.is_(False))).scalar() or 0
    )
    return {
        "comics": comics,
        "stats": stats(db),
        "changes": recent_changes(db, hours=168, limit=25),
        "alerts": {
            "unseen": int(unseen),
            "alerts": [alert_to_dict(alert) for alert in comic_alerts(db, limit=50)],
        },
        "state": export_payload(db),
    }


# ----------------------------------------------------------------------- acoes
def _apply_patch(db: Session, comic: Comic, patch: Any) -> None:
    if not isinstance(patch, dict):
        return
    if patch.get("title"):
        comic.title = str(patch["title"]).strip()
        comic.normalized_title = normalize(comic.title)
    for field in PATCHABLE_FIELDS:
        if field in patch:
            setattr(comic, field, patch[field])
    for field in PATCHABLE_FLOATS:
        if field in patch:
            setattr(comic, field, _coerce_float(patch[field]))
    if patch.get("tracked") is not None:
        comic.tracked = bool(patch["tracked"])
    db.commit()
    db.refresh(comic)


def _refresh_one(
    db: Session,
    comic: Comic,
    sources: list[str] | None,
    settings: Settings,
) -> dict[str, Any]:
    try:
        return refresh_comic(db, comic, sources=sources, settings=settings)
    except Exception as exc:  # noqa: BLE001 - devolve erro legivel para a UI
        db.rollback()
        raise ValueError(f"Falha ao atualizar precos: {exc}") from exc


def run_action(payload: dict[str, Any], *, settings: Settings | None = None) -> dict[str, Any]:
    """Aplica a acao pedida sobre o estado do navegador e devolve o estado novo."""
    settings = settings or default_settings
    action = str(payload.get("action") or "sync").strip().lower()

    db = open_state_session()
    try:
        load_state(db, payload.get("state"))
        detail: str | None = None
        extra: dict[str, Any] = {}

        if action == "add":
            comic_payload = dict(payload.get("comic") or {})
            comic_payload["scrape"] = bool(payload.get("scrape", False))
            if payload.get("sources") is not None:
                comic_payload["sources"] = payload.get("sources")
            comic, created = add_comic(db, comic_payload, settings=settings)
            detail = (
                "Quadrinho adicionado à banca" if created else "Quadrinho já estava na banca"
            )
            extra["created"] = created
            extra["key"] = comic.normalized_title

        elif action == "refresh":
            sources = payload.get("sources")
            key = payload.get("key")
            if key:
                comic = _find(db, key)
                if comic is None:
                    raise KeyError("Quadrinho não encontrado na banca")
                summary = _refresh_one(db, comic, sources, settings)
                detail = (
                    "Preços atualizados "
                    f"({summary.get('history_entries', 0)} mudança(s) registrada(s))"
                )
                extra["key"] = comic.normalized_title
            else:
                comics = list(db.execute(select(Comic)).scalars())
                total = 0
                for comic in comics:
                    try:
                        summary = _refresh_one(db, comic, sources, settings)
                        total += summary.get("history_entries", 0)
                    except ValueError:
                        continue
                detail = (
                    f"{len(comics)} quadrinho(s) reconsultado(s) · "
                    f"{total} mudança(s) no histórico"
                )

        elif action == "remove":
            comic = _find(db, payload.get("key"))
            if comic is None:
                raise KeyError("Quadrinho não encontrado na banca")
            title = comic.title
            db.delete(comic)
            db.commit()
            detail = f"'{title}' removido da banca"

        elif action == "update":
            comic = _find(db, payload.get("key"))
            if comic is None:
                raise KeyError("Quadrinho não encontrado na banca")
            _apply_patch(db, comic, payload.get("patch") or {})
            detail = "Quadrinho atualizado"
            extra["key"] = comic.normalized_title

        elif action == "ack_alerts":
            alert_id = payload.get("alert_id")
            marked = mark_alerts_seen(
                db, alert_id=int(alert_id) if alert_id is not None else None
            )
            db.commit()
            detail = f"{marked} aviso(s) marcado(s) como lido(s)"

        elif action == "import":
            incoming = payload.get("import_state") or {}
            if not isinstance(incoming, dict):
                raise ValueError("arquivo invalido: esperado um objeto JSON")
            result = import_payload(db, _normalize_export(incoming))
            _load_alerts(db, incoming.get("alerts"))
            detail = (
                f"{result['comics_created']} novo(s), "
                f"{result['comics_updated']} atualizado(s), "
                f"{result['history_added']} registro(s) de histórico"
            )
            extra["import_result"] = result

        elif action == "detail":
            comic = _find(db, payload.get("key"))
            if comic is None:
                raise KeyError("Quadrinho não encontrado na banca")
            extra["comic"] = _detail(db, comic)

        out = _view(db)
        if detail:
            out["detail"] = detail
        out.update(extra)
        return out
    finally:
        db.close()


__all__ = ["open_state_session", "load_state", "run_action"]
