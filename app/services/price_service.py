"""Regras de negocio de precos: historico de variacao, descontos e snapshots.

Regra central: **toda** mudanca de preco gera um registro em ``price_history``
contendo quanto subiu/caiu (absoluto e %) e o desconto vigente em relacao ao
preco de referencia.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Alert, Comic, Offer, PriceHistory, utcnow
from app.scrapers.base import OfferResult
from app.services.text import guess_issue, normalize, same_product


# --------------------------------------------------------------------- utils
def round_money(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def compute_discount(price: float, list_price: float | None, reference_price: float | None) -> float:
    """Desconto (%) do preco atual sobre a referencia disponivel.

    Prioriza o preco "de" da propria loja; na falta dele usa o preco de capa
    cadastrado no quadrinho.
    """
    basis = list_price if list_price and list_price > price else reference_price
    if not basis or basis <= 0 or basis <= price:
        return 0.0
    return round((basis - price) / basis * 100, 2)


def classify_change(previous: float | None, current: float) -> str:
    if previous is None:
        return "inicial"
    if current < previous:
        return "queda"
    if current > previous:
        return "alta"
    return "estavel"


# ------------------------------------------------------------------- avisos
def record_stock_alert(
    db: Session,
    comic: Comic,
    offer: Offer,
    *,
    kind: str = "volta_estoque",
    message: str | None = None,
) -> Alert:
    """Registra um aviso (ex.: produto voltou ao estoque) para a prateleira."""
    price = round_money(offer.price) if offer.price is not None else None
    preco_txt = f" por {price:.2f}".replace(".", ",") if price is not None else ""
    alert = Alert(
        comic_id=comic.id,
        offer_id=offer.id,
        kind=kind,
        message=message
        or f"{comic.title} voltou ao estoque em {offer.source_label or offer.source}{preco_txt}",
        source=offer.source,
        source_label=offer.source_label,
        seller=offer.seller,
        price=price,
        url=offer.url,
    )
    db.add(alert)
    return alert


def alert_to_dict(alert: Alert) -> dict:
    return {
        "id": alert.id,
        "comic_id": alert.comic_id,
        "offer_id": alert.offer_id,
        "kind": alert.kind,
        "message": alert.message,
        "source": alert.source,
        "source_label": alert.source_label,
        "seller": alert.seller,
        "price": round_money(alert.price),
        "url": alert.url,
        "seen": alert.seen,
        "created_at": alert.created_at,
    }


def comic_alerts(
    db: Session, *, only_unseen: bool = False, limit: int = 100
) -> list[Alert]:
    stmt = select(Alert)
    if only_unseen:
        stmt = stmt.where(Alert.seen.is_(False))
    stmt = stmt.order_by(Alert.created_at.desc(), Alert.id.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


def mark_alerts_seen(db: Session, *, alert_id: int | None = None) -> int:
    """Marca aviso(s) como lido(s). Sem ``alert_id`` marca todos."""
    stmt = select(Alert).where(Alert.seen.is_(False))
    if alert_id is not None:
        stmt = stmt.where(Alert.id == alert_id)
    alerts = list(db.execute(stmt).scalars())
    for alert in alerts:
        alert.seen = True
    return len(alerts)


def _lowest_ever(db: Session, comic_id: int, price: float | None = None) -> float | None:
    current = db.execute(
        select(func.min(PriceHistory.price)).where(PriceHistory.comic_id == comic_id)
    ).scalar()
    values = [v for v in (current, price) if v is not None]
    return min(values) if values else None


# ------------------------------------------------------------------- escrita
def record_price_change(
    db: Session,
    comic: Comic,
    price: float,
    *,
    source: str | None = None,
    previous_price: float | None = None,
    list_price: float | None = None,
) -> PriceHistory | None:
    """Grava um registro de historico **somente** se houve mudanca de preco."""
    price = round(float(price), 2)
    previous = round_money(previous_price)

    if previous is not None and abs(previous - price) < 0.001:
        return None

    delta = None if previous is None else round(price - previous, 2)
    delta_pct = None if previous in (None, 0) else round((price - previous) / previous * 100, 2)

    entry = PriceHistory(
        comic_id=comic.id,
        source=source,
        price=price,
        previous_price=previous,
        list_price=round_money(list_price),
        delta=delta,
        delta_pct=delta_pct,
        discount_pct=compute_discount(price, list_price, comic.reference_price),
        lowest_ever=_lowest_ever(db, comic.id, price),
        change_type=classify_change(previous, price),
        recorded_at=utcnow(),
    )
    db.add(entry)
    comic.updated_at = utcnow()
    return entry


def _last_consolidated(db: Session, comic_id: int) -> PriceHistory | None:
    return db.execute(
        select(PriceHistory)
        .where(PriceHistory.comic_id == comic_id, PriceHistory.source.is_(None))
        .order_by(PriceHistory.recorded_at.desc(), PriceHistory.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _last_for_source(db: Session, comic_id: int, source: str) -> PriceHistory | None:
    return db.execute(
        select(PriceHistory)
        .where(PriceHistory.comic_id == comic_id, PriceHistory.source == source)
        .order_by(PriceHistory.recorded_at.desc(), PriceHistory.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _find_same_listing(db: Session, comic_id: int, result: OfferResult) -> Offer | None:
    """Oferta ja existente da mesma loja/vendedor para o mesmo produto.

    Lojas trocam o id do anuncio (e as vezes o proprio titulo) mantendo a mesma
    oferta; sem isso o quadrinho acumularia varias linhas do mesmo vendedor.
    """
    stmt = select(Offer).where(Offer.comic_id == comic_id, Offer.source == result.source)
    if result.seller:
        stmt = stmt.where(Offer.seller == result.seller)
    for candidate in db.execute(stmt).scalars():
        if candidate.external_id != result.external_id and same_product(
            candidate.title, result.title
        ):
            return candidate
    return None


def sync_offers(
    db: Session,
    comic: Comic,
    offers: list[OfferResult],
    *,
    only_sources: set[str] | None = None,
) -> dict:
    """Upsert das ofertas coletadas e registro das mudancas de preco.

    Retorna um resumo com quantas ofertas foram vistas/criadas e quantos
    registros de historico foram gerados.
    """
    created_offers = 0
    updated_offers = 0
    history_entries: list[PriceHistory] = []

    for result in offers:
        if only_sources and result.source not in only_sources:
            continue

        offer = db.execute(
            select(Offer).where(
                Offer.comic_id == comic.id,
                Offer.source == result.source,
                Offer.external_id == result.external_id,
            )
        ).scalar_one_or_none()

        if offer is None:
            offer = _find_same_listing(db, comic.id, result)
            if offer is not None:
                # mesma loja/produto com nova identidade de anuncio: reaproveita
                offer.external_id = result.external_id

        entry: PriceHistory | None = None

        if offer is None:
            offer = Offer(
                comic_id=comic.id,
                source=result.source,
                source_label=result.source_label,
                external_id=result.external_id,
                title=result.title,
                url=result.url,
                image_url=result.image_url,
                seller=result.seller,
                condition=result.condition,
                price=round(float(result.price), 2) if result.price is not None else None,
                list_price=round_money(result.list_price),
                shipping=round_money(result.shipping),
                in_stock=result.in_stock,
                preorder=result.preorder,
                stock_changed_at=None if result.in_stock else utcnow(),
                previous_price=None,
            )
            db.add(offer)
            created_offers += 1
            db.flush()
            if offer.price is not None:
                entry = record_price_change(
                    db,
                    comic,
                    offer.price,
                    source=result.source,
                    previous_price=None,
                    list_price=offer.list_price,
                )
        else:
            was_in_stock = offer.in_stock
            previous = offer.price
            if result.price is not None and abs((offer.price or 0) - result.price) >= 0.001:
                offer.previous_price = offer.price
                offer.price = round(float(result.price), 2)
            updated_offers += 1

            offer.title = result.title
            offer.url = result.url
            offer.image_url = result.image_url or offer.image_url
            offer.seller = result.seller or offer.seller
            offer.condition = result.condition or offer.condition
            offer.list_price = round_money(result.list_price) or offer.list_price
            offer.shipping = round_money(result.shipping)
            offer.in_stock = result.in_stock
            offer.preorder = result.preorder
            offer.source_label = result.source_label or offer.source_label
            offer.updated_at = utcnow()

            # Transicao de estoque: se voltou a ficar disponivel e o quadrinho
            # esta sendo monitorado, gera um aviso para a prateleira.
            if was_in_stock != result.in_stock:
                offer.stock_changed_at = utcnow()
                if result.in_stock and not was_in_stock and comic.tracked:
                    record_stock_alert(db, comic, offer)

            if offer.price is not None:
                entry = record_price_change(
                    db,
                    comic,
                    offer.price,
                    source=result.source,
                    previous_price=previous,
                    list_price=offer.list_price,
                )

        if entry is not None:
            history_entries.append(entry)

    db.flush()

    # ---- registro consolidado (melhor preco entre as lojas) ----------------
    db.expire(comic, ["offers"])  # garante que a colecao reflita o upsert
    best = best_offer(comic.offers)
    consolidated_entry = None
    if best is not None:
        last = _last_consolidated(db, comic.id)
        previous = last.price if last else None
        consolidated_entry = record_price_change(
            db,
            comic,
            best.price,
            source=None,
            previous_price=previous,
            list_price=best.list_price,
        )
        if consolidated_entry is not None:
            history_entries.append(consolidated_entry)

    comic.last_checked_at = utcnow()
    db.flush()

    return {
        "created_offers": created_offers,
        "updated_offers": updated_offers,
        "history_entries": len(history_entries),
        "changes": [entry_to_dict(entry) for entry in history_entries],
    }


# ------------------------------------------------------------------- leitura
def best_offer(offers: list[Offer]) -> Offer | None:
    """Oferta de menor preco ENTRE as disponiveis em estoque.

    Produtos esgotados (``in_stock=False``) nao disputam o "melhor preco":
    quando nada esta disponivel, retorna ``None`` para a UI sinalizar "em falta".
    """
    available = [offer for offer in offers if offer.in_stock and offer.price and offer.price > 0]
    if not available:
        return None
    return min(available, key=lambda offer: (offer.price, offer.shipping or 0))


def comic_history(
    db: Session,
    comic_id: int,
    *,
    source: str | None = None,
    only_consolidated: bool = False,
    limit: int = 200,
) -> list[PriceHistory]:
    stmt = select(PriceHistory).where(PriceHistory.comic_id == comic_id)
    if only_consolidated:
        stmt = stmt.where(PriceHistory.source.is_(None))
    elif source is not None:
        stmt = stmt.where(PriceHistory.source == source)
    stmt = stmt.order_by(PriceHistory.recorded_at.desc(), PriceHistory.id.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


def comic_snapshot(db: Session, comic: Comic) -> dict:
    """Consolida o estado atual do quadrinho para a interface."""
    offers = sorted(comic.offers, key=lambda offer: (offer.price is None, offer.price or 0))
    best = best_offer(comic.offers)

    stats = db.execute(
        select(
            func.min(PriceHistory.price),
            func.max(PriceHistory.price),
            func.count(PriceHistory.id),
        ).where(PriceHistory.comic_id == comic.id)
    ).one()
    lowest_ever, highest_ever, history_count = stats

    lowest_row = None
    if lowest_ever is not None:
        lowest_row = db.execute(
            select(PriceHistory)
            .where(PriceHistory.comic_id == comic.id, PriceHistory.price == lowest_ever)
            .order_by(PriceHistory.recorded_at.asc())
            .limit(1)
        ).scalar_one_or_none()

    last_change = db.execute(
        select(PriceHistory)
        .where(PriceHistory.comic_id == comic.id, PriceHistory.delta.is_not(None))
        .order_by(PriceHistory.recorded_at.desc(), PriceHistory.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    current_price = round(best.price, 2) if best else None
    discount_pct = (
        compute_discount(current_price, best.list_price, comic.reference_price)
        if current_price is not None
        else 0.0
    )

    # "Lojas" = vendedores distintos (um mesmo site pode ter varios vendedores).
    stores = {
        (offer.source, offer.seller or offer.source_label or offer.source) for offer in offers
    }

    is_lowest_ever = (
        current_price is not None and lowest_ever is not None and current_price <= round(lowest_ever, 2)
    )

    return {
        "current_price": current_price,
        "current_source": best.source if best else None,
        "current_source_label": best.source_label if best else None,
        "current_list_price": round_money(best.list_price) if best else None,
        "current_offer_url": best.url if best else None,
        "current_seller": best.seller if best else None,
        "current_shipping": round_money(best.shipping) if best else None,
        "discount_pct": discount_pct,
        "reference_price": round_money(comic.reference_price),
        "lowest_ever": round_money(lowest_ever),
        "lowest_ever_at": lowest_row.recorded_at if lowest_row else None,
        "highest_ever": round_money(highest_ever),
        "is_lowest_ever": bool(is_lowest_ever),
        "savings_from_highest": (
            round(highest_ever - current_price, 2) if highest_ever and current_price else None
        ),
        "last_change": entry_to_dict(last_change) if last_change else None,
        "offer_count": len(offers),
        "source_count": len({offer.source for offer in offers}),
        "store_count": len(stores),
        "history_count": int(history_count or 0),
        "in_stock": any(offer.in_stock for offer in offers),
        "available_offer_count": len([o for o in offers if o.in_stock and o.price]),
        "out_of_stock_count": len([o for o in offers if not o.in_stock]),
        "preorder": any(offer.preorder for offer in offers),
        "preorder_count": len([o for o in offers if o.preorder]),
        "target_price": round_money(comic.target_price),
        "target_reached": bool(
            comic.target_price and current_price is not None and current_price <= comic.target_price
        ),
        "last_checked_at": comic.last_checked_at,
    }


def entry_to_dict(entry: PriceHistory | None) -> dict | None:
    if entry is None:
        return None
    return {
        "id": entry.id,
        "comic_id": entry.comic_id,
        "source": entry.source,
        "price": round_money(entry.price),
        "previous_price": round_money(entry.previous_price),
        "list_price": round_money(entry.list_price),
        "delta": round_money(entry.delta),
        "delta_pct": entry.delta_pct,
        "discount_pct": entry.discount_pct,
        "lowest_ever": round_money(entry.lowest_ever),
        "change_type": entry.change_type,
        "recorded_at": entry.recorded_at,
    }


def offer_to_dict(offer: Offer, reference_price: float | None = None) -> dict:
    return {
        "id": offer.id,
        "source": offer.source,
        "source_label": offer.source_label,
        "title": offer.title,
        "url": offer.url,
        "image_url": offer.image_url,
        "seller": offer.seller,
        "condition": offer.condition,
        "price": round_money(offer.price),
        "previous_price": round_money(offer.previous_price),
        "list_price": round_money(offer.list_price),
        "discount_pct": (
            compute_discount(offer.price, offer.list_price, reference_price)
            if offer.price is not None
            else 0.0
        ),
        "shipping": round_money(offer.shipping),
        "in_stock": offer.in_stock,
        "preorder": offer.preorder,
        "stock_changed_at": offer.stock_changed_at,
        "updated_at": offer.updated_at,
    }


def comic_to_dict(db: Session, comic: Comic, *, history_points: int = 0) -> dict:
    """Serializa o quadrinho com seus indicadores e historico."""
    snapshot = comic_snapshot(db, comic)
    payload = {
        "id": comic.id,
        "title": comic.title,
        "normalized_title": comic.normalized_title,
        "publisher": comic.publisher,
        "issue": comic.issue,
        "volume": comic.volume,
        "description": comic.description,
        "cover_url": comic.cover_url,
        "tags": comic.tags,
        "tracked": comic.tracked,
        "created_at": comic.created_at,
        "updated_at": comic.updated_at,
        "offers": [
            offer_to_dict(offer, comic.reference_price)
            for offer in sorted(
                comic.offers, key=lambda item: (item.price is None, item.price or 0)
            )
        ],
        "snapshot": snapshot,
    }
    if history_points:
        history = comic_history(db, comic.id, only_consolidated=True, limit=history_points)
        payload["sparkline"] = [
            {"price": round_money(row.price), "recorded_at": row.recorded_at}
            for row in reversed(history)
        ]
    return payload


# ---------------------------------------------------------------- criacao/CRUD
def find_comic(db: Session, title: str) -> Comic | None:
    normalized = normalize(title)
    if not normalized:
        return None
    return db.execute(
        select(Comic).where(Comic.normalized_title == normalized)
    ).scalar_one_or_none()


def create_comic(
    db: Session,
    title: str,
    *,
    publisher: str | None = None,
    issue: str | None = None,
    volume: str | None = None,
    description: str | None = None,
    cover_url: str | None = None,
    reference_price: float | None = None,
    target_price: float | None = None,
    tags: str | None = None,
) -> Comic:
    comic = Comic(
        title=title.strip(),
        normalized_title=normalize(title),
        publisher=publisher,
        issue=issue or guess_issue(title),
        volume=volume or guess_issue(title),
        description=description,
        cover_url=cover_url,
        reference_price=round_money(reference_price),
        target_price=round_money(target_price),
        tags=tags,
        tracked=True,
    )
    db.add(comic)
    db.flush()
    return comic


def recent_changes(db: Session, hours: int = 24, limit: int = 30) -> list[dict]:
    """Ultimas mudancas de preco registradas (para o painel inicial)."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(
        select(PriceHistory, Comic.title)
        .join(Comic, Comic.id == PriceHistory.comic_id)
        .where(PriceHistory.recorded_at >= since, PriceHistory.delta.is_not(None))
        .order_by(PriceHistory.recorded_at.desc())
        .limit(limit)
    ).all()
    return [
        {**entry_to_dict(entry), "comic_title": title}  # type: ignore[misc]
        for entry, title in rows
    ]
