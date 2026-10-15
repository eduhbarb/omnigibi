"""Exportacao/importacao da prateleira em JSON (backup e portabilidade).

O arquivo exportado carrega os quadrinhos com o historico consolidado e por loja.
As preferencias de interface sao adicionadas pelo cliente (ficam no navegador) e
ignoradas na importacao, que faz **merge**: casa quadrinhos pela identidade
normalizada e descarta observacoes de historico ja existentes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Alert, Comic, PriceHistory, utcnow
from app.services.price_service import comic_history, create_comic, find_comic

FORMAT_ID = "banca-quadrinhos.export"
FORMAT_ALIASES = {FORMAT_ID, "banca-quadrinhos.shelf"}
SCHEMA_VERSION = 1


def _dt_key(value: datetime | None) -> datetime | None:
    """Chave de comparacao para datas, normalizando fuso e microssegundos."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0)


def _serialize_history(comic: Comic, entries: list[PriceHistory]) -> list[dict[str, Any]]:
    return [
        {
            "source": entry.source,
            "price": entry.price,
            "previous_price": entry.previous_price,
            "list_price": entry.list_price,
            "delta": entry.delta,
            "delta_pct": entry.delta_pct,
            "discount_pct": entry.discount_pct,
            "lowest_ever": entry.lowest_ever,
            "change_type": entry.change_type,
            "recorded_at": entry.recorded_at.isoformat() if entry.recorded_at else None,
        }
        for entry in entries
    ]


def export_payload(db: Session) -> dict[str, Any]:
    """Monta o JSON de exportacao com todos os quadrinhos e seus historicos."""
    comics = list(db.execute(select(Comic).order_by(Comic.created_at.asc())).scalars())
    payload_comics = []
    for comic in comics:
        payload_comics.append(
            {
                "title": comic.title,
                "normalized_title": comic.normalized_title,
                "publisher": comic.publisher,
                "issue": comic.issue,
                "volume": comic.volume,
                "description": comic.description,
                "cover_url": comic.cover_url,
                "tags": comic.tags,
                "tracked": comic.tracked,
                "reference_price": comic.reference_price,
                "target_price": comic.target_price,
                "history": _serialize_history(
                    comic, comic_history(db, comic.id, limit=5000)
                ),
            }
        )

    alerts = list(
        db.execute(select(Alert).order_by(Alert.created_at.asc())).scalars()
    )
    titles = {comic.id: comic.title for comic in comics}

    return {
        "format": FORMAT_ID,
        "schema_version": SCHEMA_VERSION,
        "app_version": settings.version,
        "exported_at": utcnow().isoformat(),
        "comics": payload_comics,
        "alerts": [
            {
                "comic_title": titles.get(alert.comic_id),
                "kind": alert.kind,
                "message": alert.message,
                "source": alert.source,
                "source_label": alert.source_label,
                "seller": alert.seller,
                "price": alert.price,
                "url": alert.url,
                "seen": alert.seen,
                "created_at": alert.created_at.isoformat() if alert.created_at else None,
            }
            for alert in alerts
        ],
    }


def _coerce_float(value: Any) -> float | None:
    if value is None:
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


def import_payload(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    """Faz merge de um arquivo exportado na base. Levanta ``ValueError`` se invalido."""
    if not isinstance(data, dict):
        raise ValueError("arquivo invalido: esperado um objeto JSON")
    fmt = data.get("format")
    if fmt not in FORMAT_ALIASES:
        raise ValueError("arquivo invalido: campo 'format' desconhecido")
    try:
        schema_version = int(data.get("schema_version", 0))
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version > SCHEMA_VERSION:
        raise ValueError(
            f"arquivo em versao mais nova (schema {schema_version}); atualize o app"
        )

    comics_created = 0
    comics_updated = 0
    history_added = 0
    history_skipped = 0

    for raw_comic in data.get("comics", []) or []:
        if not isinstance(raw_comic, dict):
            continue
        title = (raw_comic.get("title") or "").strip()
        if not title:
            continue

        comic = find_comic(db, title)
        if comic is None:
            comic = create_comic(
                db,
                title,
                publisher=raw_comic.get("publisher"),
                issue=raw_comic.get("issue"),
                volume=raw_comic.get("volume"),
                description=raw_comic.get("description"),
                cover_url=raw_comic.get("cover_url"),
                reference_price=_coerce_float(raw_comic.get("reference_price")),
                target_price=_coerce_float(raw_comic.get("target_price")),
                tags=raw_comic.get("tags"),
            )
            comics_created += 1
        else:
            # merge conservador: so preenche campos vazios, nunca sobrescreve o local
            for field in (
                "publisher",
                "issue",
                "volume",
                "description",
                "cover_url",
                "tags",
            ):
                if getattr(comic, field) is None and raw_comic.get(field) is not None:
                    setattr(comic, field, raw_comic.get(field))
            for field in ("reference_price", "target_price"):
                if getattr(comic, field) is None:
                    setattr(comic, field, _coerce_float(raw_comic.get(field)))
            if raw_comic.get("tracked"):
                comic.tracked = True
            comics_updated += 1

        existing = {
            (entry.source, _dt_key(entry.recorded_at), _coerce_float(entry.price))
            for entry in comic.price_history
        }

        for raw_entry in raw_comic.get("history", []) or []:
            if not isinstance(raw_entry, dict):
                continue
            price = _coerce_float(raw_entry.get("price"))
            recorded_at = _parse_dt(raw_entry.get("recorded_at"))
            if price is None:
                continue
            key = (raw_entry.get("source"), _dt_key(recorded_at), price)
            if key in existing:
                history_skipped += 1
                continue
            existing.add(key)
            db.add(
                PriceHistory(
                    comic_id=comic.id,
                    source=raw_entry.get("source"),
                    price=price,
                    previous_price=_coerce_float(raw_entry.get("previous_price")),
                    list_price=_coerce_float(raw_entry.get("list_price")),
                    delta=_coerce_float(raw_entry.get("delta")),
                    delta_pct=_coerce_float(raw_entry.get("delta_pct")),
                    discount_pct=_coerce_float(raw_entry.get("discount_pct")),
                    lowest_ever=_coerce_float(raw_entry.get("lowest_ever")),
                    change_type=raw_entry.get("change_type") or "inicial",
                    recorded_at=recorded_at or utcnow(),
                )
            )
            history_added += 1

    db.commit()

    return {
        "comics_created": comics_created,
        "comics_updated": comics_updated,
        "history_added": history_added,
        "history_skipped": history_skipped,
    }
