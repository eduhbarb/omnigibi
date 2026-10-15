"""Busca inteligente de precos: agrega fontes, pontua relevancia e agrupa ofertas.

O "sistema inteligente" faz cinco coisas:
1. dispara todos os coletores habilitados em paralelo
   (e, se a busca principal nao trouxer nada bom, tenta variacoes dela);
2. normaliza titulos preservando a identidade do quadrinho
   (volume, numero de edicao, "ano") — Vol. 1 e Vol. 2 sao produtos distintos;
3. pontua a relevancia de cada oferta e nunca mistura volumes diferentes;
4. agrupa ofertas equivalentes de lojas diferentes no mesmo produto;
5. ranqueia pelo menor preco, cruzando com o historico ja registrado na banca
   e devolvendo tambem os "relacionados" (outros volumes/edicoes) em vez de
   simplesmente descarta-los.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, settings as default_settings
from app.models import Comic
from app.scrapers import BaseScraper, OfferResult, ScraperReport, build_scrapers
from app.services.price_service import comic_snapshot, compute_discount
from app.services.text import (
    base_title,
    expand_query_variants,
    extract_volume,
    guess_publisher,
    normalize,
    relevance,
    same_product,
)


# ------------------------------------------------------------------- coleta
def run_scrapers(
    queries: str | list[str],
    *,
    sources: list[str] | None = None,
    settings: Settings | None = None,
    limit: int | None = None,
    max_workers: int = 6,
) -> list[ScraperReport]:
    """Executa os coletores em paralelo, tolerando falhas individuais.

    Aceita uma ou varias consultas (variacoes de busca); as ofertas repetidas
    entre variacoes sao deduplicadas por (fonte, id externo).
    """
    settings = settings or default_settings
    limit = limit or settings.max_results_per_source
    query_list = [queries] if isinstance(queries, str) else [q for q in queries if q and q.strip()]
    scrapers: list[BaseScraper] = build_scrapers(settings, sources)
    if not scrapers or not query_list:
        return []

    collected: dict[tuple[str, str], OfferResult] = {}
    errors: dict[str, str] = {}
    elapsed: dict[str, int] = {}

    tasks = [(scraper, query) for scraper in scrapers for query in query_list]
    workers = max(1, min(max_workers, len(tasks)))
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scraper")
    try:
        futures = {
            executor.submit(scraper.safe_search, query, limit): scraper for scraper, query in tasks
        }
        timeout = settings.http_timeout + 5
        for future in as_completed(futures, timeout=timeout + 5):
            scraper = futures[future]
            try:
                report = future.result(timeout=timeout)
            except Exception as exc:  # noqa: BLE001 - timeout ou falha inesperada
                errors[scraper.name] = f"tempo esgotado: {exc}"
                continue

            elapsed[scraper.name] = elapsed.get(scraper.name, 0) + report.elapsed_ms
            if report.ok:
                for offer in report.offers:
                    collected.setdefault((offer.source, offer.external_id), offer)
                errors.pop(scraper.name, None)
            else:
                errors.setdefault(scraper.name, report.error or "falha desconhecida")
    except Exception:  # pragma: no cover - garantia de nao travar a requisicao
        for scraper, _query in tasks:
            errors.setdefault(scraper.name, "busca interrompida")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        for scraper in scrapers:
            scraper.close()

    reports: list[ScraperReport] = []
    for scraper in scrapers:
        offers = [offer for (source, _id), offer in collected.items() if source == scraper.name]
        reports.append(
            ScraperReport(
                source=scraper.name,
                label=scraper.label,
                offers=offers,
                error=errors.get(scraper.name),
                elapsed_ms=elapsed.get(scraper.name, 0),
            )
        )
    return reports


def merge_reports(*groups: list[ScraperReport]) -> list[ScraperReport]:
    """Combina relatorios de execucoes diferentes (busca principal + variacoes)."""
    order: list[str] = []
    merged: dict[str, ScraperReport] = {}

    for group in groups:
        for report in group:
            if report.source not in merged:
                merged[report.source] = ScraperReport(
                    source=report.source,
                    label=report.label,
                    offers=list(report.offers),
                    error=report.error,
                    elapsed_ms=report.elapsed_ms,
                )
                order.append(report.source)
                continue

            current = merged[report.source]
            seen = {(offer.source, offer.external_id) for offer in current.offers}
            current.offers.extend(
                offer for offer in report.offers if (offer.source, offer.external_id) not in seen
            )
            current.elapsed_ms += report.elapsed_ms
            if report.ok:
                current.error = None
            elif current.error is None:
                current.error = report.error

    return [merged[source] for source in order]


# --------------------------------------------------------------- agrupamento
def _clusterize(offers: list[OfferResult]) -> list[list[OfferResult]]:
    """Agrupa ofertas equivalentes (mesmo quadrinho em lojas diferentes).

    Usa :func:`same_product`, que exige volume compativel e titulos realmente
    equivalentes — variacoes de titulo entre lojas entram no mesmo grupo, mas
    livros diferentes (ou volumes diferentes) nunca.
    """
    clusters: list[list[OfferResult]] = []
    for offer in offers:
        placed = False
        for cluster in clusters:
            if any(same_product(member.title, offer.title) for member in cluster):
                cluster.append(offer)
                placed = True
                break
        if not placed:
            clusters.append([offer])
    return clusters


def _cluster_volume(cluster: list[OfferResult]) -> str | None:
    """Volume do grupo: o mais votado entre as ofertas, ponderado pela relevancia."""
    votes: dict[str, float] = {}
    for offer in cluster:
        volume = extract_volume(offer.title)
        if volume is None:
            continue
        votes[volume] = votes.get(volume, 0.0) + max(offer.relevance, 0.05)
    if not votes:
        return None
    return max(votes.items(), key=lambda item: item[1])[0]


def _representative_title(cluster: list[OfferResult]) -> str:
    """Titulo que melhor descreve o produto.

    Prioriza relevancia e, depois, o titulo **mais completo** (mais longo) —
    titulos curtos de marketplace costumam omitir volume/edicao.
    """
    return max(
        cluster,
        key=lambda offer: (offer.relevance, len(offer.title), -(offer.price or 0)),
    ).title


def _guess_publisher(offers: list[OfferResult], fallback: str | None = None) -> str | None:
    for offer in offers:
        publisher = (offer.raw or {}).get("publisher")
        if publisher:
            return str(publisher)
    # Nenhuma fonte informou a editora: tenta deduzir do titulo/vendedor/rotulo.
    for offer in offers:
        found = guess_publisher(offer.title, offer.seller, offer.source_label)
        if found:
            return found
    return fallback


def _link_comic(db: Session, key_title: str, cache: dict[str, Comic | None]) -> Comic | None:
    """Encontra (se existir) o quadrinho da banca correspondente ao produto."""
    normalized = normalize(key_title)
    if normalized in cache:
        return cache[normalized]

    comics = db.execute(select(Comic)).scalars().all()
    best: Comic | None = None
    for comic in comics:
        if comic.normalized_title == normalized or same_product(comic.normalized_title, normalized):
            best = comic
            break

    cache[normalized] = best
    return best


def _build_product(
    db: Session,
    cluster: list[OfferResult],
    *,
    cache: dict[str, Comic | None],
    volume_match: bool,
    related: bool,
) -> dict[str, Any]:
    # Ordena com os disponiveis primeiro e, depois, pelo menor preco.
    offers = sorted(
        cluster,
        key=lambda item: (not item.in_stock, item.price is None, item.price or 0, item.shipping or 0),
    )
    key_title = _representative_title(cluster)
    priced = [offer for offer in offers if offer.price is not None]
    available = [offer for offer in priced if offer.in_stock]
    # A "faixa" de precos considera os itens a venda; se nada estiver a venda,
    # cai para o ultimo preco conhecido (para nao exibir faixa vazia).
    price_basis = available or priced
    prices = [offer.price for offer in price_basis]
    best = min(available, key=lambda item: (item.price, item.shipping or 0)) if available else None

    # Preco de referencia do produto: o maior "preco de" visto entre as lojas.
    list_prices = [offer.list_price for offer in offers if offer.list_price]
    reference_price = round(max(list_prices), 2) if list_prices else None
    comic = _link_comic(db, key_title, cache)

    lowest_ever = None
    lowest_ever_at = None
    is_lowest_ever = False
    history_count = 0
    if comic is not None:
        snapshot = comic_snapshot(db, comic)
        lowest_ever = snapshot["lowest_ever"]
        lowest_ever_at = snapshot["lowest_ever_at"]
        history_count = snapshot["history_count"]
        if best is not None:
            is_lowest_ever = lowest_ever is not None and best.price <= round(lowest_ever, 2)

    volume = _cluster_volume(cluster)
    best_relevance = max(offer.relevance for offer in offers)

    return {
        "title": key_title,
        "normalized_title": normalize(key_title),
        "base_group": base_title(key_title),
        "volume": volume,
        "volume_match": volume_match,
        "publisher": _guess_publisher(cluster),
        "image_url": next((o.image_url for o in cluster if o.image_url), None),
        "offer_count": len(offers),
        "source_count": len({offer.source for offer in offers}),
        "store_count": len(
            {(offer.source, offer.seller or offer.source_label) for offer in offers}
        ),
        "best": best.to_dict() if best else None,
        "offers": [offer.to_dict() for offer in offers],
        "in_stock": bool(available),
        "available_offer_count": len(available),
        "out_of_stock_count": len([offer for offer in offers if not offer.in_stock]),
        "preorder": any(offer.preorder for offer in offers),
        "preorder_count": len([offer for offer in offers if offer.preorder]),
        "price_min": round(min(prices), 2) if prices else None,
        "price_max": round(max(prices), 2) if prices else None,
        "price_avg": round(sum(prices) / len(prices), 2) if prices else None,
        "best_total": (
            round(min(offer.price + (offer.shipping or 0) for offer in available), 2)
            if available
            else None
        ),
        "savings_vs_max": (
            round(max(prices) - min(prices), 2) if len(prices) > 1 else 0.0
        ),
        "reference_price": reference_price,
        "discount_pct": (
            compute_discount(best.price, best.list_price, reference_price) if best else 0.0
        ),
        "relevance": best_relevance,
        "related": related,
        "comic_id": comic.id if comic else None,
        "tracked": bool(comic and comic.tracked),
        "lowest_ever": lowest_ever,
        "lowest_ever_at": lowest_ever_at,
        "is_lowest_ever": is_lowest_ever,
        "history_count": history_count,
    }


def smart_search(
    db: Session,
    query: str,
    *,
    sources: list[str] | None = None,
    limit: int | None = None,
    settings: Settings | None = None,
    include_reports: bool = True,
) -> dict[str, Any]:
    """Busca agregada e ranqueada. Nao persiste nada no banco."""
    settings = settings or default_settings
    query = (query or "").strip()
    if not query:
        return {
            "query": "",
            "results": [],
            "related": [],
            "sources": [],
            "total_offers": 0,
            "message": "Informe o nome de um quadrinho.",
        }

    query_volume = extract_volume(query)
    variants = expand_query_variants(query)

    # 1) busca principal
    reports = run_scrapers(
        variants[0], sources=sources, limit=limit, settings=settings
    )

    def _score(offers: list[OfferResult]) -> None:
        for offer in offers:
            offer.relevance = relevance(query, offer.title)

    for report in reports:
        _score(report.offers)

    best_found = max(
        (offer.relevance for report in reports for offer in report.offers), default=0.0
    )

    # 2) variacoes de busca, somente quando a principal foi fraca (recall extra)
    used_variants = variants[:1]
    if best_found < settings.relevance_threshold and len(variants) > 1:
        extra_reports = run_scrapers(
            variants[1:], sources=sources, limit=limit, settings=settings
        )
        for report in extra_reports:
            _score(report.offers)
        reports = merge_reports(reports, extra_reports)
        used_variants = variants

    # 3) pontuacao, corte e agrupamento
    scored: list[OfferResult] = []
    for report in reports:
        for offer in report.offers:
            if offer.relevance >= settings.related_threshold:
                scored.append(offer)

    scored.sort(key=lambda item: (-item.relevance, item.price is None, item.price or 0))
    clusters = _clusterize(scored)

    cache: dict[str, Comic | None] = {}
    results: list[dict[str, Any]] = []
    related: list[dict[str, Any]] = []

    for cluster in clusters:
        best_relevance = max(offer.relevance for offer in cluster)
        volume = _cluster_volume(cluster)
        volume_match = query_volume is None or volume == query_volume
        strict = best_relevance >= settings.relevance_threshold

        product = _build_product(
            db,
            cluster,
            cache=cache,
            volume_match=volume_match,
            related=not strict,
        )
        (results if strict else related).append(product)

    # volume pedido primeiro; no empate, relevancia e depois preco
    def _price_key(item: dict[str, Any]) -> float:
        value = item.get("price_min")
        return value if value is not None else float("inf")

    results.sort(
        key=lambda item: (not item["volume_match"], -item["relevance"], _price_key(item))
    )
    related.sort(key=lambda item: (-item["relevance"], _price_key(item)))

    payload: dict[str, Any] = {
        "query": query,
        "normalized_query": normalize(query),
        "query_volume": query_volume,
        "query_variants": used_variants,
        "results": results,
        "related": related,
        "total_offers": len(scored),
        "product_count": len(results),
        "related_count": len(related),
        "sources_ok": [report.source for report in reports if report.ok],
        "sources_failed": [
            {"source": report.source, "label": report.label, "error": report.error}
            for report in reports
            if not report.ok
        ],
    }
    if include_reports:
        payload["sources"] = [report.to_dict() for report in reports]
    return payload
