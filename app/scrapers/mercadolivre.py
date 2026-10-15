"""Coletor do Mercado Livre (usa a API publica de busca, formato JSON)."""

from __future__ import annotations

from typing import Any

from app.scrapers.base import BaseScraper, OfferResult, ScraperError

API_BASE = "https://api.mercadolibre.com"
SITE_ID = "MLB"  # Mercado Livre Brasil


class MercadoLivreScraper(BaseScraper):
    name = "mercadolivre"
    label = "Mercado Livre"
    homepage = "https://www.mercadolivre.com.br"

    def search(self, query: str, limit: int | None = None) -> list[OfferResult]:
        limit = min(limit or self.settings.max_results_per_source, 50)
        # Filtra pela categoria de HQs/revistas para reduzir ruido.
        url = (
            f"{API_BASE}/sites/{SITE_ID}/search"
            f"?q={query.strip()}&limit={limit}&condition=not_used"
        )
        try:
            response = self.client.get(url)
        except Exception as exc:  # noqa: BLE001 - normalizamos para ScraperError
            raise ScraperError(f"falha de rede: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise ScraperError(f"API respondeu HTTP {response.status_code}")

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ScraperError("resposta nao era JSON valido") from exc

        offers: list[OfferResult] = []
        for item in payload.get("results", [])[:limit]:
            price = self.parse_money(item.get("price"))
            if not price:
                continue
            list_price = self.parse_money(item.get("original_price"))
            if list_price and list_price <= price:
                list_price = None

            shipping = None
            ship_info = item.get("shipping") or {}
            if ship_info.get("free_shipping"):
                shipping = 0.0

            try:
                offers.append(
                    OfferResult(
                        source=self.name,
                        source_label=self.label,
                        title=str(item.get("title", "")).strip(),
                        price=price,
                        list_price=list_price,
                        url=item.get("permalink") or "",
                        external_id=str(item.get("id", "")),
                        image_url=(item.get("thumbnail") or "").replace("http://", "https://"),
                        seller=(item.get("seller") or {}).get("nickname"),
                        condition="novo" if item.get("condition") == "new" else item.get("condition"),
                        shipping=shipping,
                        in_stock=item.get("available_quantity", 1) != 0,
                        raw={"sold_quantity": item.get("sold_quantity")},
                    )
                )
            except ScraperError:
                continue
        return offers
