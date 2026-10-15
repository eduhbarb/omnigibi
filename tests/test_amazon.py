"""Testes do coletor da Amazon (parsing do HTML, sem rede)."""

from __future__ import annotations

import pytest

from app.config import settings
from app.scrapers.amazon import AmazonScraper
from app.scrapers.base import ScraperError

# Recorte fiel do layout da busca da Amazon (amazon.com.br/s?k=...).
SEARCH_HTML = """
<div class="s-main-slot s-result-list">
  <div data-component-type="s-search-result" data-asin="6525951348" class="s-result-item">
    <div data-cy="title-recipe">
      <h2 class="a-size-medium"><a class="a-link-normal"
        href="/Batman-robin-ano-vol-01/dp/6525951348/ref=sr_1_1?dib=abc"><span>Batman &amp; robin: ano um vol. 01</span></a></h2>
    </div>
    <div class="a-row a-size-base a-color-base">
      <span class="a-price"><span aria-hidden="true"><span class="a-price-symbol">R$</span>
        <span class="a-price-whole">49<span class="a-price-decimal">,</span></span>
        <span class="a-price-fraction">28</span></span><span class="a-offscreen">R$ 49,28</span></span>
      <span class="a-price a-text-price"><span aria-hidden="true">R$ 54,90</span>
        <span class="a-offscreen">R$ 54,90</span></span>
    </div>
    <img class="s-image" src="https://m.media-amazon.com/images/I/vol01.jpg" />
  </div>

  <div data-component-type="s-search-result" data-asin="6562528127" class="s-result-item">
    <div data-cy="title-recipe">
      <h2 class="a-size-medium"><a class="a-link-normal"
        href="/Batman-robin-ano-vol-02/dp/6562528127/ref=sr_1_2?dib=abc"><span>Batman &amp; robin: ano um vol. 02</span></a></h2>
    </div>
    <div class="a-row">
      <span class="a-price"><span class="a-offscreen">R$ 47,90</span></span>
      <!-- preco "de" de outro vendedor, MENOR que o preco atual: deve ser descartado -->
      <span class="a-price a-text-price"><span class="a-offscreen">R$ 23,95</span></span>
    </div>
    <img class="s-image" src="https://m.media-amazon.com/images/I/vol02.jpg" />
  </div>

  <div data-component-type="s-search-result" data-asin="9999999999" class="s-result-item">
    <div data-cy="title-recipe"><h2><a href="/Esgotado/dp/9999999999/ref=sr_1_3"><span>Sandman edicao absoluta</span></a></h2></div>
    <!-- sem preco: item deve ser ignorado -->
  </div>
</div>
"""

CAPTCHA_HTML = """
<div id="captchacharacters">
  <h4>Digite os caracteres que aparecem na imagem</h4>
  <form action="/errors/validateCaptcha">...</form>
</div>
"""


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


def test_parse_extrai_titulo_preco_asin_e_link_limpo():
    scraper = AmazonScraper(settings)
    try:
        offers = scraper.parse_html(SEARCH_HTML, limit=10)
    finally:
        scraper.close()

    # o item sem preco e ignorado
    assert len(offers) == 2

    vol1, vol2 = offers
    assert vol1.title == "Batman & robin: ano um vol. 01"
    assert vol1.price == 49.28
    assert vol1.list_price == 54.90
    assert vol1.external_id == "6525951348"
    assert vol1.url == "https://www.amazon.com.br/dp/6525951348"
    assert vol1.image_url == "https://m.media-amazon.com/images/I/vol01.jpg"
    assert vol1.seller == "Amazon"
    assert vol1.discount_pct > 0

    # preco "de" menor que o preco atual e ruido de layout -> descartado
    assert vol2.price == 47.90
    assert vol2.list_price is None


def test_parse_detecta_captcha():
    scraper = AmazonScraper(settings)
    try:
        with pytest.raises(ScraperError, match="captcha"):
            scraper.parse_html(CAPTCHA_HTML, limit=10)
    finally:
        scraper.close()


def test_search_monta_url_codificada_e_respeita_o_limite(monkeypatch):
    scraper = AmazonScraper(settings)
    capturado: dict[str, str] = {}

    def fake_fetch(url: str, **kwargs):
        capturado["url"] = url
        return _FakeResponse(SEARCH_HTML)

    monkeypatch.setattr(scraper, "fetch", fake_fetch)
    try:
        offers = scraper.search("Batman & Robin: Ano Um", limit=1)
    finally:
        scraper.close()

    assert capturado["url"].startswith("https://www.amazon.com.br/s?k=")
    assert "Batman+%26+Robin%3A+Ano+Um" in capturado["url"]
    assert "i=stripbooks" in capturado["url"]
    assert len(offers) == 1  # limit respeitado


def test_parse_inclui_item_esgotado_marcado():
    """Item sem preco com sinal de indisponibilidade entra marcado como em falta."""
    oos_html = """
    <div class="s-main-slot s-result-list">
      <div data-component-type="s-search-result" data-asin="6525940206" class="s-result-item">
        <div data-cy="title-recipe"><h2><a href="/Absolute-batman-01/dp/6525940206/ref=sr_1_1">
          <span>Absolute batman 01 (Volume 1)</span></a></h2></div>
        <div class="a-row a-size-base a-color-secondary">
          <span class="a-color-secondary">Nenhuma opção de compra em destaque R$ 69,90 (5 ofertas)</span>
        </div>
      </div>
    </div>
    """
    scraper = AmazonScraper(settings)
    try:
        offers = scraper.parse_html(oos_html, limit=10)
    finally:
        scraper.close()

    assert len(offers) == 1
    oferta = offers[0]
    assert oferta.title == "Absolute batman 01 (Volume 1)"
    assert oferta.price is None
    assert oferta.in_stock is False
    assert oferta.external_id == "6525940206"


def test_parse_detecta_pre_venda():
    pre_html = """
    <div class="s-main-slot s-result-list">
      <div data-component-type="s-search-result" data-asin="1234567890" class="s-result-item">
        <div data-cy="title-recipe"><h2><a href="/Absolute/dp/1234567890/ref=sr_1_1">
          <span>Absolute Batman 01</span></a></h2></div>
        <div class="a-row"><span class="a-color-price">Pré-venda</span></div>
        <div class="a-row"><span class="a-price"><span class="a-offscreen">R$ 19,90</span></span></div>
      </div>
    </div>
    """
    scraper = AmazonScraper(settings)
    try:
        offers = scraper.parse_html(pre_html, limit=10)
    finally:
        scraper.close()

    assert len(offers) == 1
    assert offers[0].price == 19.90
    assert offers[0].in_stock is True
    assert offers[0].preorder is True


def test_amazon_esta_no_catalogo_de_fontes():
    from app.scrapers import available_sources

    amazon = next(source for source in available_sources() if source["name"] == "amazon")
    assert amazon["label"] == "Amazon"
    assert amazon["homepage"].endswith("amazon.com.br")
