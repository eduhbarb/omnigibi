"""Testes do coletor da Panini (parsing do HTML Magento, sem rede)."""

from __future__ import annotations

from app.config import settings
from app.scrapers.panini import PaniniScraper

# Recorte fiel do layout da busca da Panini (Magento 2).
SEARCH_HTML = """
<ul class="products list items product-items">
  <li class="item product product-item">
    <div class="product-item-info" id="product-item-info_408637">
      <a class="product photo product-item-photo" href="https://panini.com.br/batman-cataclismo">
        <img class="product-image-photo" src="https://cdn.panini/batman-cataclismo.jpg" />
      </a>
      <div class="product details product-item-details">
        <strong class="product name product-item-name">
          <a class="product-item-link" href="https://panini.com.br/batman-cataclismo">Batman: Cataclismo</a>
        </strong>
        <span class="infobase-label-presale">Pré-venda</span>
        <div class="price-box price-final_price" data-role="priceBox"
             data-product-id="408637" data-price-box="product-id-408637">
          <span class="price-container price-final_price tax weee">
            <span class="price-wrapper" data-price-amount="166.9" data-price-type="finalPrice">
              <span class="price">R$ 166,90</span>
            </span>
          </span>
          <span class="old-price">
            <span class="price-wrapper" data-price-amount="199.9" data-price-type="oldPrice">
              <span class="price">R$ 199,90</span>
            </span>
          </span>
        </div>
      </div>
    </div>
  </li>

  <li class="item product product-item">
    <div class="product-item-info" id="product-item-info_777777" data-product-id="777777">
      <a class="product photo product-item-photo" href="https://panini.com.br/batman-esgotado">
        <img class="product-image-photo" src="https://cdn.panini/batman-esgotado.jpg" />
      </a>
      <div class="product details product-item-details">
        <strong class="product name product-item-name">
          <a class="product-item-link" href="https://panini.com.br/batman-esgotado">Batman: Esgotado</a>
        </strong>
        <!-- sem price-box: produto indisponivel -->
        <div class="stock unavailable"><span>Indisponível</span></div>
      </div>
    </div>
  </li>
</ul>
"""


def test_parse_extrai_preco_exato_e_produto_esgotado():
    scraper = PaniniScraper(settings)
    try:
        offers = scraper.parse_html(SEARCH_HTML, limit=10)
    finally:
        scraper.close()

    assert len(offers) == 2

    disponivel, esgotado = offers
    assert disponivel.title == "Batman: Cataclismo"
    assert disponivel.price == 166.9
    assert disponivel.list_price == 199.9
    assert disponivel.external_id == "408637"
    assert disponivel.url == "https://panini.com.br/batman-cataclismo"
    assert disponivel.image_url == "https://cdn.panini/batman-cataclismo.jpg"
    assert disponivel.in_stock is True
    assert disponivel.preorder is True  # rotulo "Pré-venda"

    # Produto em falta entra marcado, sem preco — nao e descartado.
    assert esgotado.title == "Batman: Esgotado"
    assert esgotado.price is None
    assert esgotado.in_stock is False
    assert esgotado.preorder is False
    assert esgotado.external_id == "777777"


def test_panini_esta_no_catalogo_de_fontes():
    from app.scrapers import available_sources

    panini = next(source for source in available_sources() if source["name"] == "panini")
    assert panini["label"] == "Panini Store"
    assert panini["homepage"].endswith("panini.com.br")
