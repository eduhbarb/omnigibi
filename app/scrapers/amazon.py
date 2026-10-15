"""Coletor da Amazon (amazon.com.br) via scraping de HTML.

A Amazon nao expoe busca publica sem chave, entao usamos a pagina de resultados
(`/s?k=...`) e extraimos os blocos `data-component-type="s-search-result"`.

Cuidados tratados aqui:
- pagina de captcha/robot check -> :class:`ScraperError` (a interface avisa);
- itens em falta (sem preco) NAO sao descartados: entram marcados como
  ``in_stock=False`` para a prateleira avisar quando voltarem;
- itens sem preco e sem sinal de indisponibilidade (patrocinados/vitrine) sao
  ignorados;
- o "preco de" as vezes vem de outro vendedor e e menor que o preco atual,
  entao e descartado para nao gerar desconto falso.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote_plus

from app.scrapers.base import BaseScraper, OfferResult, ScraperError


def _strip_accents(text: str) -> str:
    """Remove acentos para comparar marcadores de texto sem depender de acentuacao."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))

SEARCH_URL = "https://www.amazon.com.br/s?k={query}&i=stripbooks"

# Sinais de que a Amazon barrou a requisicao.
_BLOCK_MARKERS = (
    "captchacharacters",
    "digite os caracteres",
    "api-services-support@amazon.com",
    "para continuar, confirme que",
    "robot check",
)

# Sinais de item indisponivel (sem oferta em destaque).
_OUT_OF_STOCK_MARKERS = (
    "nenhuma opcao de compra em destaque",
    "no featured offers available",
    "temporariamente fora de estoque",
    "temporariamente indisponivel",
    "fora de estoque",
    "produto indisponivel",
    "indisponivel no momento",
    "esgotado",
    "sem estoque",
)

# Sinais de pre-venda (produto ainda nao lancado).
_PREORDER_MARKERS = (
    "pre-venda",
    "pre venda",
    "preorder",
    "pre-order",
    "sera lancado em",
    "a ser lancado",
)

_ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})", re.IGNORECASE)


class AmazonScraper(BaseScraper):
    name = "amazon"
    label = "Amazon"
    homepage = "https://www.amazon.com.br"

    ITEM_SELECTOR = 'div[data-component-type="s-search-result"]'
    TITLE_SELECTORS = (
        "h2 span",
        '[data-cy="title-recipe"] a span',
        "h2 a span",
        "h2",
    )
    PRICE_SELECTORS = (
        "span.a-price:not(.a-text-price) span.a-offscreen",
        "span.a-price span.a-offscreen",
        ".a-price .a-offscreen",
    )

    def search(self, query: str, limit: int | None = None) -> list[OfferResult]:
        limit = min(limit or self.settings.max_results_per_source, 50)
        url = SEARCH_URL.format(query=quote_plus(query.strip()))
        response = self.fetch(url, retries=1)
        return self.parse_html(response.text, limit)

    # ----------------------------------------------------------------- parsing
    def parse_html(self, html: str, limit: int) -> list[OfferResult]:
        from bs4 import BeautifulSoup

        lowered = html.lower()
        if any(marker in lowered for marker in _BLOCK_MARKERS):
            raise ScraperError("Amazon pediu verificacao anti-robô (captcha)")
        if "s-search-result" not in html and "a-price" not in html:
            raise ScraperError("layout da busca da Amazon nao reconhecido")

        soup = BeautifulSoup(html, "lxml")
        offers: list[OfferResult] = []

        for node in soup.select(self.ITEM_SELECTOR):
            if len(offers) >= limit:
                break

            title = self._extract_title(node)
            if not title:
                continue

            price = self._extract_price(node)
            if price is None and not self._looks_out_of_stock(node):
                # Sem preco e sem sinal de indisponibilidade: vitrine/patrocinado.
                continue

            list_price = self._extract_list_price(node, price)
            href = self._extract_link(node)
            asin = node.get("data-asin") or ""
            if not asin and href:
                match = _ASIN_RE.search(href)
                asin = match.group(1) if match else ""

            image = node.select_one("img.s-image")
            image_url = (image.get("src") if image else None) or None

            try:
                offers.append(
                    OfferResult(
                        source=self.name,
                        source_label=self.label,
                        title=title,
                        price=price,
                        list_price=list_price,
                        url=href or "",
                        external_id=asin,
                        image_url=image_url,
                        seller="Amazon",
                        condition="novo",
                        in_stock=price is not None,
                        preorder=self._looks_preorder(node),
                        raw={"asin": asin},
                    )
                )
            except ScraperError:
                continue

        return offers

    def _looks_out_of_stock(self, node) -> bool:
        """Item sem preco que a Amazon marca como indisponivel."""
        lowered = _strip_accents(node.get_text(" ", strip=True).lower())
        return any(marker in lowered for marker in _OUT_OF_STOCK_MARKERS)

    def _looks_preorder(self, node) -> bool:
        """Item em pre-venda (ainda nao lancado)."""
        lowered = _strip_accents(node.get_text(" ", strip=True).lower())
        return any(marker in lowered for marker in _PREORDER_MARKERS)

    def _extract_title(self, node) -> str:
        for selector in self.TITLE_SELECTORS:
            found = node.select_one(selector)
            if found:
                text = found.get_text(" ", strip=True)
                if len(text) > 3:
                    return text
        return ""

    def _extract_price(self, node) -> float | None:
        for selector in self.PRICE_SELECTORS:
            found = node.select_one(selector)
            if not found:
                continue
            price = self.parse_money(found.get_text(" ", strip=True))
            if price:
                return price

        # Fallback: monta o preco a partir das partes "inteira" + "centavos".
        whole = node.select_one("span.a-price-whole")
        if whole:
            fraction = node.select_one("span.a-price-fraction")
            text = whole.get_text(strip=True).rstrip(",. ")
            if fraction:
                text = f"{text},{fraction.get_text(strip=True)}"
            return self.parse_money(text)
        return None

    def _extract_list_price(self, node, price: float | None) -> float | None:
        """Preco "de" apenas quando faz sentido (maior que o preco atual)."""
        if price is None:
            return None
        for selector in (
            "span.a-price.a-text-price span.a-offscreen",
            ".basisPrice .a-offscreen",
            "span.a-text-price span.a-offscreen",
        ):
            found = node.select_one(selector)
            if not found:
                continue
            candidate = self.parse_money(found.get_text(" ", strip=True))
            if candidate and candidate > price:
                return candidate
        return None

    def _extract_link(self, node) -> str:
        link = node.select_one("h2 a") or node.select_one("a.a-link-normal")
        href = (link.get("href") if link else "") or ""
        if not href:
            return ""
        if href.startswith("/"):
            # /Nome-do-livro/dp/ASIN/ref=... -> link limpo e estavel
            match = _ASIN_RE.search(href)
            if match:
                return f"https://www.amazon.com.br/dp/{match.group(1)}"
            href = f"https://www.amazon.com.br{href}"
        return href.split("/ref=")[0]
