"""Operacoes da banca: adicionar quadrinhos, atualizar precos e estatisticas."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, settings as default_settings
from app.models import Comic, Offer, PriceHistory, utcnow
from app.scrapers import OfferResult
from app.scrapers.simulator import SimuladorScraper
from app.services.price_service import (
    comic_to_dict,
    create_comic,
    find_comic,
    record_price_change,
    sync_offers,
)
from app.services.search_service import run_scrapers
from app.services.text import extract_volume, normalize, same_product, similarity, volumes_compatible


def _offer_result_from_payload(payload: dict[str, Any]) -> OfferResult | None:
    """Reconstroi um OfferResult vindo do front-end (resultado de busca)."""
    try:
        raw_price = payload.get("price")
        in_stock = bool(payload.get("in_stock", True))
        return OfferResult(
            source=payload["source"],
            source_label=payload.get("source_label") or payload["source"],
            title=payload.get("title", ""),
            price=float(raw_price) if raw_price is not None else None,
            list_price=payload.get("list_price"),
            url=payload.get("url", ""),
            external_id=payload.get("external_id") or "",
            image_url=payload.get("image_url"),
            seller=payload.get("seller"),
            condition=payload.get("condition"),
            shipping=payload.get("shipping"),
            in_stock=in_stock,
            preorder=bool(payload.get("preorder", False)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _search_query(comic: Comic) -> str:
    """Titulo + editora para consultar as lojas.

    A editora so e acrescentada quando ainda nao aparece no titulo — varios
    anuncios (ex.: marketplace/simulador) ja trazem "... - Editora X" no titulo,
    e repetir a editora muda o termo de busca e cria ofertas duplicadas.
    """
    titulo = (comic.title or "").strip()
    editora = (comic.publisher or "").strip()
    if not editora:
        return titulo
    if normalize(editora) and normalize(editora) in normalize(titulo):
        return titulo
    return f"{titulo} {editora}"


def refresh_comic(
    db: Session,
    comic: Comic,
    *,
    sources: list[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Reconsulta as lojas, atualiza ofertas e grava o historico de variacoes."""
    settings = settings or default_settings
    query = _search_query(comic)

    reports = run_scrapers(query, sources=sources, settings=settings)
    collected: list[OfferResult] = []
    for report in reports:
        for offer in report.offers:
            # Somente o MESMO quadrinho entra na banca: mesma obra, mesmo volume.
            # (relevancia sozinha deixaria passar "Robin: Ano Um" numa busca por
            # "Batman & Robin: Ano Um", contaminando precos e historico.)
            if not same_product(comic.title, offer.title):
                continue
            if not volumes_compatible(extract_volume(comic.title), extract_volume(offer.title)):
                continue
            offer.relevance = similarity(comic.title, offer.title)
            collected.append(offer)

    summary = sync_offers(db, comic, collected)

    # Primeira coleta define capa e preco de referencia.
    if comic.cover_url is None:
        comic.cover_url = next((offer.image_url for offer in collected if offer.image_url), None)
    if comic.reference_price is None:
        list_prices = [offer.list_price for offer in collected if offer.list_price]
        if list_prices:
            comic.reference_price = round(max(list_prices), 2)

    db.commit()
    db.refresh(comic)
    return {
        "comic_id": comic.id,
        "collected": len(collected),
        "sources": [report.to_dict() for report in reports],
        **summary,
    }


def add_comic(
    db: Session,
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> tuple[Comic, bool]:
    """Adiciona um quadrinho a banca. Retorna (comic, criado_agora)."""
    settings = settings or default_settings
    title = (payload.get("title") or "").strip()
    if not title:
        raise ValueError("Titulo do quadrinho e obrigatorio")

    existing = find_comic(db, title)
    if existing is not None:
        return existing, False

    offers_payload = payload.get("offers") or []
    reference_price = payload.get("reference_price")

    if reference_price is None:
        list_prices = [
            float(offer["list_price"])
            for offer in offers_payload
            if isinstance(offer, dict) and offer.get("list_price")
        ]
        if list_prices:
            reference_price = max(list_prices)

    comic = create_comic(
        db,
        title,
        publisher=payload.get("publisher"),
        issue=payload.get("issue"),
        volume=payload.get("volume"),
        description=payload.get("description"),
        cover_url=payload.get("cover_url")
        or next(
            (
                offer.get("image_url")
                for offer in offers_payload
                if isinstance(offer, dict) and offer.get("image_url")
            ),
            None,
        ),
        reference_price=reference_price,
        target_price=payload.get("target_price"),
        tags=payload.get("tags"),
    )

    # Persiste as ofertas ja conhecidas para a banca nascer com preco.
    initial = [item for item in (_offer_result_from_payload(o) for o in offers_payload) if item]
    if initial:
        sync_offers(db, comic, initial)

    should_scrape = payload.get("scrape", True)
    if should_scrape or not initial:
        db.commit()
        refresh_comic(
            db,
            comic,
            sources=payload.get("sources"),
            settings=settings,
        )
    else:
        db.commit()
        db.refresh(comic)

    return comic, True


def refresh_all(
    db: Session,
    *,
    sources: list[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Atualiza todos os quadrinhos monitorados da banca."""
    settings = settings or default_settings
    comics = list(db.execute(select(Comic).where(Comic.tracked.is_(True))).scalars())
    results: list[dict[str, Any]] = []
    total_changes = 0
    for comic in comics:
        try:
            summary = refresh_comic(db, comic, sources=sources, settings=settings)
            total_changes += summary.get("history_entries", 0)
            results.append(
                {
                    "comic_id": comic.id,
                    "title": comic.title,
                    "collected": summary.get("collected", 0),
                    "history_entries": summary.get("history_entries", 0),
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001 - um problema nao derruba os outros
            db.rollback()
            results.append(
                {"comic_id": comic.id, "title": comic.title, "error": f"{type(exc).__name__}: {exc}"}
            )
    return {"comics": len(comics), "total_history_entries": total_changes, "results": results}


def seed_demo(db: Session, *, settings: Settings | None = None, limit: int = 15) -> int:
    """Popula a banca com quadrinhos classicos usando a fonte simulada."""
    settings = settings or default_settings
    created = 0
    for item in SimuladorScraper.catalog()[:limit]:
        if find_comic(db, item["title"]):
            continue
        comic = create_comic(
            db,
            item["title"],
            publisher=item["publisher"],
            reference_price=item["reference_price"],
        )
        db.commit()

        scraper = SimuladorScraper(settings)
        report = scraper.safe_search(item["title"], limit=4)
        scraped = [offer for offer in report.offers]
        if scraped:
            sync_offers(db, comic, scraped)
        else:  # pragma: no cover - simulador nunca falha
            record_price_change(
                db, comic, item["base_price"], previous_price=None, list_price=item["reference_price"]
            )
        db.commit()
        created += 1
    return created


def stats(db: Session) -> dict[str, Any]:
    """Indicadores gerais para o cabecalho da banca."""
    total_comics = db.execute(select(func.count(Comic.id))).scalar() or 0
    tracked_comics = (
        db.execute(select(func.count(Comic.id)).where(Comic.tracked.is_(True))).scalar() or 0
    )
    total_offers = db.execute(select(func.count(Offer.id))).scalar() or 0
    # "registros" conta apenas as mudancas reais do MELHOR preco (linha consolidada,
    # source IS NULL). Sem isso somaria tambem uma linha por loja e os registros
    # "inicial", inflando o numero e divergindo do painel de variacoes.
    total_history = (
        db.execute(
            select(func.count(PriceHistory.id)).where(
                PriceHistory.source.is_(None), PriceHistory.delta.is_not(None)
            )
        ).scalar()
        or 0
    )
    total_observations = db.execute(select(func.count(PriceHistory.id))).scalar() or 0
    drops = (
        db.execute(
            select(func.count(PriceHistory.id)).where(
                PriceHistory.source.is_(None), PriceHistory.change_type == "queda"
            )
        ).scalar()
        or 0
    )
    rises = (
        db.execute(
            select(func.count(PriceHistory.id)).where(
                PriceHistory.source.is_(None), PriceHistory.change_type == "alta"
            )
        ).scalar()
        or 0
    )
    biggest_drop = db.execute(
        select(func.min(PriceHistory.delta_pct)).where(PriceHistory.source.is_(None))
    ).scalar()

    best_savings = None
    lowest_rows = db.execute(
        select(PriceHistory.comic_id, func.min(PriceHistory.price)).group_by(PriceHistory.comic_id)
    ).all()
    for comic_id, lowest in lowest_rows:
        comic = db.get(Comic, comic_id)
        if comic is None or not comic.reference_price or not lowest:
            continue
        saving = round(comic.reference_price - lowest, 2)
        if best_savings is None or saving > best_savings["savings"]:
            best_savings = {
                "comic_id": comic.id,
                "title": comic.title,
                "savings": saving,
                "lowest_ever": round(lowest, 2),
            }

    return {
        "total_comics": int(total_comics),
        "tracked_comics": int(tracked_comics),
        "total_offers": int(total_offers),
        "total_history": int(total_history),
        "total_observations": int(total_observations),
        "drops": int(drops),
        "rises": int(rises),
        "biggest_drop_pct": round(biggest_drop, 2) if biggest_drop is not None else None,
        "best_savings": best_savings,
        "generated_at": utcnow(),
    }


def banca_payload(db: Session, *, sparkline_points: int = 20) -> list[dict[str, Any]]:
    comics = list(
        db.execute(select(Comic).order_by(Comic.updated_at.desc(), Comic.id.desc())).scalars()
    )
    return [comic_to_dict(db, comic, history_points=sparkline_points) for comic in comics]
