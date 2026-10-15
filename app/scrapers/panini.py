"""Coletor da Panini Store (panini.com.br).

A loja fica atras de um CDN (Fastly) que bloqueia clientes HTTP genericos com
``HTTP 403`` por *fingerprint* de TLS. Esse bloqueio atinge ate a pagina inicial
e responde independente do ``User-Agent``. A saida e usar a biblioteca
``curl_cffi``, que imita o handshake TLS/HTTP2 de um Chrome real.

A Panini roda Magento 2, entao os itens vem em ``li.product-item`` com titulo em
``a.product-item-link`` e preco exato no atributo ``data-price-amount``.

Produto esgotado entra como oferta com ``in_stock=False`` (e sem preco), em vez
de ser descartado.
"""

from __future__ import annotations

import time
import unicodedata
from urllib.parse import quote_plus

from app.scrapers.base import BaseScraper, OfferResult, ScraperError


def _strip_accents(text: str) -> str:
    """Remove acentos para comparar marcadores de texto sem depender de acentuacao."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))

SEARCH_URL = "https://panini.com.br/catalogsearch/result/?q={query}"

# Marcadores de indisponibilidade no texto do item (Magento/pt-BR).
_OUT_OF_STOCK_MARKERS = (
    "esgotado",
    "indisponivel",
    "fora de estoque",
    "sem estoque",
    "out of stock",
    "produto esgotado",
)

# Marcadores de pre-venda (produto ainda nao lancado, mas ja compravel).
_PREORDER_MARKERS = ("pre-venda", "pre venda", "preorder", "pre-order")


class PaniniScraper(BaseScraper):
    name = "panini"
    label = "Panini Store"
    homepage = "https://panini.com.br"

    ITEM_SELECTOR = "li.product-item"

    def search(self, query: str, limit: int | None = None) -> list[OfferResult]:
        limit = min(limit or self.settings.max_results_per_source, 50)
        url = SEARCH_URL.format(query=quote_plus(query.strip()))
        html = self._get(url)
        return self.parse_html(html, limit)

    # ------------------------------------------------------------------- HTTP
    def _get(self, url: str, retries: int = 1) -> str:
        try:
            from curl_cffi import requests as cffi
        except ImportError as exc:  # pragma: no cover - dependencia opcional
            raise ScraperError(
                "Panini exige o pacote curl_cffi (instale com: pip install curl_cffi)"
            ) from exc

        attempt = 0
        while True:
            try:
                response = cffi.get(
                    url,
                    impersonate="chrome",
                    timeout=self.settings.http_timeout,
                    headers={
                        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                        "Accept": (
                            "text/html,application/xhtml+xml,application/xml;q=0.9,"
                            "image/avif,image/webp,*/*;q=0.8"
                        ),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - curl_cffi usa excecoes proprias
                raise ScraperError(f"falha de rede na Panini: {type(exc).__name__}") from exc

            if response.status_code in (429, 503) and attempt < retries:
                attempt += 1
                time.sleep(0.9 * attempt)
                continue
            if response.status_code == 403:
                raise ScraperError("acesso bloqueado pela Panini (HTTP 403)")
            if response.status_code >= 400:
                raise ScraperError(f"resposta HTTP {response.status_code} da Panini")
            return response.text

    # ---------------------------------------------------------------- parsing
    def parse_html(self, html: str, limit: int) -> list[OfferResult]:
        from bs4 import BeautifulSoup

        if "product-item" not in html and "catalogsearch" not in html:
            raise ScraperError("layout da busca da Panini nao reconhecido")

        soup = BeautifulSoup(html, "lxml")
        offers: list[OfferResult] = []

        for node in soup.select(self.ITEM_SELECTOR):
            if len(offers) >= limit:
                break

            title = self._extract_title(node)
            if not title:
                continue

            price = self._extract_price(node)
            in_stock = price is not None and not self._looks_out_of_stock(node)
            preorder = self._looks_preorder(node)

            try:
                offers.append(
                    OfferResult(
                        source=self.name,
                        source_label=self.label,
                        title=title,
                        price=price,
                        list_price=self._extract_list_price(node, price),
                        url=self._extract_link(node),
                        external_id=self._extract_id(node),
                        image_url=self._extract_image(node),
                        seller="Panini Store",
                        condition="novo",
                        in_stock=in_stock,
                        preorder=preorder,
                    )
                )
            except ScraperError:
                continue

        return offers

    def _extract_title(self, node) -> str:
        for selector in (
            ".product-item-name a.product-item-link",
            ".product-item-link",
            ".product-item-name",
        ):
            found = node.select_one(selector)
            if found:
                text = found.get_text(" ", strip=True)
                if len(text) > 2:
                    return text
        return ""

    def _extract_price(self, node) -> float | None:
        holder = node.select_one("[data-price-amount]")
        if holder is not None:
            price = self.parse_money(holder.get("data-price-amount"))
            if price:
                return price
        found = node.select_one(".price-box .price, .price")
        if found:
            return self.parse_money(found.get_text(" ", strip=True))
        return None

    def _extract_list_price(self, node, price: float | None) -> float | None:
        if price is None:
            return None
        for selector in (
            '[data-price-type="oldPrice"] [data-price-amount]',
            ".old-price .price",
            ".price-box .old-price",
        ):
            found = node.select_one(selector)
            if not found:
                continue
            raw = found.get("data-price-amount") or found.get_text(" ", strip=True)
            candidate = self.parse_money(raw)
            if candidate and candidate > price:
                return candidate
        return None

    def _extract_link(self, node) -> str:
        link = node.select_one("a.product-item-link") or node.select_one("a")
        return (link.get("href") if link else "") or ""

    def _extract_image(self, node) -> str | None:
        image = node.select_one("img.product-image-photo") or node.select_one("img")
        if image is None:
            return None
        return image.get("src") or image.get("data-src")

    def _extract_id(self, node) -> str:
        holder = node.select_one("[data-product-id]")
        if holder is not None and holder.get("data-product-id"):
            return str(holder.get("data-product-id"))
        sku = node.select_one("[data-product-sku]")
        if sku is not None and sku.get("data-product-sku"):
            return str(sku.get("data-product-sku"))
        return ""

    def _looks_out_of_stock(self, node) -> bool:
        if node.select_one(".stock.unavailable, [data-role='stock-unavailable']"):
            return True
        text = _strip_accents(node.get_text(" ", strip=True).lower())
        return any(marker in text for marker in _OUT_OF_STOCK_MARKERS)

    def _looks_preorder(self, node) -> bool:
        """Produto em pre-venda (rotulo Magento 'infobase-label-presale')."""
        if node.select_one(".infobase-label-presale, [class*='presale']"):
            return True
        text = _strip_accents(node.get_text(" ", strip=True).lower())
        return any(marker in text for marker in _PREORDER_MARKERS)
