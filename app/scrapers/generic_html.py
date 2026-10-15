"""Coletor generico de HTML (BeautifulSoup) dirigido por CSS selectors.

Permite adicionar novas lojas sem escrever codigo: basta descrever os seletores
em ``app/scrapers/sites.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, OfferResult, ScraperError

SITES_FILE = Path(__file__).with_name("sites.json")


def load_site_configs(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Le a configuracao declarativa das lojas HTML."""
    file_path = path or SITES_FILE
    if not file_path.exists():
        return {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - config quebrada
        raise ScraperError(f"sites.json invalido: {exc}") from exc
    return {name: cfg for name, cfg in data.items() if isinstance(cfg, dict)}


class HtmlSiteScraper(BaseScraper):
    """Scraper de loja baseado em CSS selectors declarativos."""

    def __init__(self, settings, config: dict[str, Any], name: str | None = None) -> None:
        super().__init__(settings)
        self.config = config
        self.name = name or config.get("name") or "html-site"
        self.label = config.get("label") or self.name
        self.homepage = config.get("homepage", "")

    # ----------------------------------------------------------------- utils
    def _select_text(self, node, selector: str | None) -> str:
        if not selector:
            return ""
        found = node.select_one(selector)
        return found.get_text(" ", strip=True) if found else ""

    def _absolute(self, url: str | None) -> str:
        if not url:
            return ""
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("http"):
            return url
        base = self.homepage.rstrip("/")
        return f"{base}/{url.lstrip('/')}"

    def parse_html(self, html: str, limit: int) -> list[OfferResult]:
        """Extrai ofertas a partir do HTML da pagina de resultados (testavel isoladamente)."""
        selectors = self.config.get("selectors") or {}
        item_selector = selectors.get("item")
        if not item_selector:
            raise ScraperError("configuracao sem seletor de item")

        soup = BeautifulSoup(html, "lxml")
        offers: list[OfferResult] = []

        for node in soup.select(item_selector)[:limit]:
            price_node = node.select_one(selectors.get("price") or "")
            price_attr = self.config.get("price_attribute")
            raw_price = ""
            if price_node is not None:
                raw_price = (
                    price_node.get(price_attr, "")
                    if price_attr
                    else price_node.get_text(" ", strip=True)
                )

            block_text = node.get_text(" ", strip=True)
            list_price, price = self.parse_money_range(raw_price or block_text)
            price = price or self.parse_money(raw_price)
            if not price:
                continue

            link_node = node.select_one(selectors.get("link") or "a")
            href = link_node.get("href") if link_node else None
            image_node = node.select_one(selectors.get("image") or "img")
            image = None
            if image_node is not None:
                image = image_node.get("src") or image_node.get("data-src")

            try:
                offers.append(
                    OfferResult(
                        source=self.name,
                        source_label=self.label,
                        title=self._select_text(node, selectors.get("title")) or block_text[:200],
                        price=price,
                        list_price=list_price,
                        url=self._absolute(href),
                        image_url=self._absolute(image),
                        seller=self._select_text(node, selectors.get("seller")),
                        condition="novo",
                    )
                )
            except ScraperError:
                continue

        return offers

    def search(self, query: str, limit: int | None = None) -> list[OfferResult]:
        limit = min(limit or int(self.config.get("limit") or self.settings.max_results_per_source), 50)
        template = self.config.get("search_url")
        if not template:
            raise ScraperError("configuracao sem search_url")

        from urllib.parse import quote_plus

        url = template.format(query=quote_plus(query.strip()))
        response = self.fetch(url)
        return self.parse_html(response.text, limit)


def build_site_scrapers(settings, only: list[str] | None = None) -> list[HtmlSiteScraper]:
    """Instancia os coletores HTML habilitados na configuracao."""
    scrapers: list[HtmlSiteScraper] = []
    for name, config in load_site_configs().items():
        if only is not None and name not in only:
            continue
        if only is None and not config.get("enabled", True):
            continue
        scrapers.append(HtmlSiteScraper(settings, config, name=name))
    return scrapers
