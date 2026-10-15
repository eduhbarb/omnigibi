"""Testes do sistema de busca: agrupamento, ranqueamento e tolerancia a falhas."""

from __future__ import annotations

from app.config import settings
from app.scrapers.base import BaseScraper, OfferResult, ScraperError
from app.services import search_service
from app.services.price_service import create_comic, sync_offers
from app.services.search_service import smart_search


class FakeScraper(BaseScraper):
    """Coletor de teste: devolve ofertas fixas ou falha de proposito."""

    def __init__(self, name: str, label: str, offers: list[OfferResult] | None = None, error=None):
        super().__init__(settings)
        self.name = name
        self.label = label
        self._offers = offers or []
        self._error = error

    def search(self, query: str, limit: int | None = None) -> list[OfferResult]:
        if self._error:
            raise ScraperError(self._error)
        return list(self._offers)


def _offer(source: str, title: str, price: float, *, list_price: float | None = None):
    return OfferResult(
        source=source,
        source_label=source.upper(),
        title=title,
        price=price,
        list_price=list_price,
        url=f"https://{source}.test/{abs(hash(title + source)) % 9999}",
    )


def _patch(monkeypatch, scrapers):
    monkeypatch.setattr(search_service, "build_scrapers", lambda *a, **k: scrapers)


def test_busca_agrupa_ofertas_equivalentes_e_ranqueia_por_preco(db, monkeypatch):
    scraper_a = FakeScraper(
        "loja_a",
        "Loja A",
        [
            _offer("loja_a", "Watchmen - Edicao Definitiva", 89.90, list_price=129.90),
            _offer("loja_a", "Sandman - Edicao Definitiva Vol. 1", 150.0),
        ],
    )
    scraper_b = FakeScraper(
        "loja_b", "Loja B", [_offer("loja_b", "Watchmen Edicao Definitiva Capa Dura", 74.90)]
    )
    _patch(monkeypatch, [scraper_a, scraper_b])

    payload = smart_search(db, "Watchmen")

    assert payload["product_count"] == 1
    product = payload["results"][0]
    assert product["offer_count"] == 2
    assert product["source_count"] == 2
    assert product["price_min"] == 74.90
    assert product["price_max"] == 89.90
    assert product["savings_vs_max"] == 15.00
    # a oferta mais barata deve ser destacada como melhor
    assert product["best"]["source"] == "loja_b"
    # desconto do produto usa o maior preco "de" visto entre as lojas
    assert product["reference_price"] == 129.90
    assert product["discount_pct"] == 42.34
    assert product["offers"][0]["price"] == 74.90


def test_busca_separa_produtos_diferentes(db, monkeypatch):
    scraper = FakeScraper(
        "loja_a",
        "Loja A",
        [
            _offer("loja_a", "Watchmen - Edicao Definitiva", 89.90),
            _offer("loja_a", "Watchmen - Absolute Edition", 399.90),
        ],
    )
    _patch(monkeypatch, [scraper])

    payload = smart_search(db, "Watchmen")
    assert payload["product_count"] == 2
    assert payload["results"][0]["price_min"] == 89.90


def test_busca_descarta_ofertas_irrelevantes(db, monkeypatch):
    scraper = FakeScraper(
        "loja_a",
        "Loja A",
        [
            _offer("loja_a", "Watchmen - Edicao Definitiva", 89.90),
            _offer("loja_a", "Caderno universitario 10 materias", 19.90),
        ],
    )
    _patch(monkeypatch, [scraper])

    payload = smart_search(db, "Watchmen")
    assert payload["total_offers"] == 1
    assert payload["product_count"] == 1


def test_falha_de_uma_fonte_nao_derruba_a_busca(db, monkeypatch):
    ok = FakeScraper("loja_a", "Loja A", [_offer("loja_a", "Watchmen - Edicao Definitiva", 89.90)])
    quebrada = FakeScraper("loja_b", "Loja B", error="bloqueado pelo site (HTTP 403)")
    _patch(monkeypatch, [ok, quebrada])

    payload = smart_search(db, "Watchmen")

    assert payload["product_count"] == 1
    assert payload["sources_ok"] == ["loja_a"]
    assert payload["sources_failed"][0]["error"] == "bloqueado pelo site (HTTP 403)"


def test_busca_aponta_menor_preco_ja_registrado(db, monkeypatch):
    comic = create_comic(db, "Watchmen - Edicao Definitiva", reference_price=129.90)
    sync_offers(db, comic, [_offer("loja_a", "Watchmen - Edicao Definitiva", 69.90)])
    db.commit()

    scraper = FakeScraper(
        "loja_a", "Loja A", [_offer("loja_a", "Watchmen - Edicao Definitiva", 79.90)]
    )
    _patch(monkeypatch, [scraper])

    payload = smart_search(db, "Watchmen")
    product = payload["results"][0]

    assert product["comic_id"] == comic.id
    assert product["tracked"] is True
    assert product["lowest_ever"] == 69.90
    assert product["is_lowest_ever"] is False  # 79.90 nao bate o recorde de 69.90
    assert product["history_count"] == 2


def test_busca_vazia_nao_consulta_fontes(db):
    payload = smart_search(db, "   ")
    assert payload["results"] == []
    assert "message" in payload


# --------------------------------------------------------- identidade/volumes
def test_busca_separa_volumes_e_traz_o_pedido_primeiro(db, monkeypatch):
    """Regressao: buscar \"...Ano Um\" so devolvia o volume 2."""
    scraper = FakeScraper(
        "loja_a",
        "Loja A",
        [
            _offer("loja_a", "Batman & Robin: Ano Um Vol. 02", 47.90),
            _offer("loja_a", "Batman & Robin: Ano Um Vol. 01", 54.90),
        ],
    )
    _patch(monkeypatch, [scraper])

    payload = smart_search(db, "Batman & Robin: Ano Um")

    assert payload["query_volume"] == "1"
    # o volume 1 existe, e o primeiro, e o volume 2 nao foi fundido com ele
    assert payload["product_count"] == 1
    assert payload["results"][0]["volume"] == "1"
    assert payload["results"][0]["volume_match"] is True
    assert payload["results"][0]["price_min"] == 54.90
    assert [item["volume"] for item in payload["related"]] == ["2"]
    assert payload["related"][0]["volume_match"] is False


def test_busca_agrupa_o_mesmo_volume_entre_lojas_e_escolhe_o_menor_preco(db, monkeypatch):
    loja_a = FakeScraper(
        "loja_a", "Loja A", [_offer("loja_a", "Batman & Robin: Ano Um Vol. 01", 54.90)]
    )
    loja_b = FakeScraper(
        "loja_b", "Loja B", [_offer("loja_b", "Batman & robin: ano um vol. 01", 49.28)]
    )
    _patch(monkeypatch, [loja_a, loja_b])

    payload = smart_search(db, "Batman & Robin: Ano Um")
    product = payload["results"][0]

    assert payload["product_count"] == 1
    assert product["offer_count"] == 2
    assert product["source_count"] == 2
    assert product["best"]["source"] == "loja_b"
    assert product["best"]["price"] == 49.28


def test_busca_nao_funde_livros_diferentes(db, monkeypatch):
    scraper = FakeScraper(
        "loja_a",
        "Loja A",
        [
            _offer("loja_a", "Batman & Robin: Ano Um Vol. 01", 54.90),
            _offer("loja_a", "Robin: Ano Um", 69.90),
        ],
    )
    _patch(monkeypatch, [scraper])

    payload = smart_search(db, "Batman & Robin: Ano Um")
    titulos = [item["title"] for item in payload["results"]]

    assert "Batman & Robin: Ano Um Vol. 01" in titulos
    assert "Robin: Ano Um" in titulos
    assert all(item["offer_count"] == 1 for item in payload["results"])


def test_busca_usa_variacoes_quando_a_consulta_principal_falha(db, monkeypatch):
    """Se a consulta literal nao trouxer nada, variacoes dela sao tentadas."""

    class VarianteScraper(BaseScraper):
        name = "loja_a"
        label = "Loja A"

        def __init__(self):
            super().__init__(settings)
            self.consultas: list[str] = []

        def search(self, query: str, limit: int | None = None):
            self.consultas.append(query)
            if "&" in query:  # a consulta original nao acha nada
                return []
            return [_offer("loja_a", "Batman & Robin: Ano Um Vol. 01", 54.90)]

    scraper = VarianteScraper()
    _patch(monkeypatch, [scraper])

    payload = smart_search(db, "Batman & Robin: Ano Um")

    assert len(scraper.consultas) > 1
    assert payload["product_count"] == 1
    assert len(payload["query_variants"]) > 1


def test_atualizacao_nao_mistura_volumes_no_historico(db, monkeypatch):
    from app.services.banca_service import refresh_comic

    comic = create_comic(db, "Batman & Robin: Ano Um Vol. 01", publisher="Panini")
    db.commit()

    scraper = FakeScraper(
        "loja_a",
        "Loja A",
        [
            _offer("loja_a", "Batman & Robin: Ano Um Vol. 01", 54.90),
            _offer("loja_a", "Batman & Robin: Ano Um Vol. 02", 47.90),
            _offer("loja_a", "Robin: Ano Um", 69.90),  # outro livro
            _offer("loja_a", "Batman Renascido", 42.70),  # outro livro
        ],
    )
    _patch(monkeypatch, [scraper])

    summary = refresh_comic(db, comic, sources=["loja_a"])
    db.refresh(comic)

    assert summary["collected"] == 1  # so o proprio volume entra
    titulos = {offer.title for offer in comic.offers}
    assert titulos == {"Batman & Robin: Ano Um Vol. 01"}


def test_consulta_de_atualizacao_nao_repete_a_editora():
    from app.models import Comic
    from app.services.banca_service import _search_query

    # titulo sem editora: a editora ajuda a busca
    assert _search_query(Comic(title="Sandman Vol. 1", publisher="Panini")) == "Sandman Vol. 1 Panini"
    # titulo que ja cita a editora: nao repete (evita ofertas duplicadas)
    assert (
        _search_query(Comic(title="Batman & Robin: Ano Um - Editora Independente",
                            publisher="Editora Independente"))
        == "Batman & Robin: Ano Um - Editora Independente"
    )
    assert _search_query(Comic(title="Watchmen", publisher=None)) == "Watchmen"


def test_atualizacao_repetida_nao_duplica_ofertas(db, monkeypatch):
    from app.services.banca_service import refresh_comic

    comic = create_comic(db, "Batman & Robin: Ano Um - Editora Independente",
                         publisher="Editora Independente")
    db.commit()

    scraper = FakeScraper(
        "loja_a",
        "Loja A",
        [_offer("loja_a", "Batman & Robin: Ano Um - Editora Independente", 64.70)],
    )
    _patch(monkeypatch, [scraper])

    refresh_comic(db, comic, sources=["loja_a"])
    refresh_comic(db, comic, sources=["loja_a"])
    db.refresh(comic)

    assert len(comic.offers) == 1  # mesma oferta, atualizada (nao duplicada)


def test_oferta_com_novo_id_mas_mesma_loja_e_produto_nao_duplica(db, monkeypatch):
    """A loja troca o id/titulo do anuncio: a linha e reaproveitada, nao duplicada."""
    from app.services.banca_service import refresh_comic

    comic = create_comic(db, "Batman & Robin: Ano Um Vol. 01", publisher="Panini")
    db.commit()

    vendido = "Sebo do Heroi"
    primeira = _offer("loja_a", "Batman & Robin: Ano Um Vol. 01", 54.90)
    primeira.seller = vendido
    scraper = FakeScraper("loja_a", "Loja A", [primeira])
    _patch(monkeypatch, [scraper])
    refresh_comic(db, comic, sources=["loja_a"])
    db.refresh(comic)
    assert len(comic.offers) == 1

    reanunciado = _offer("loja_a", "Batman & Robin: Ano Um Vol. 01 - Panini", 49.90)
    reanunciado.seller = vendido
    _patch(monkeypatch, [FakeScraper("loja_a", "Loja A", [reanunciado])])
    refresh_comic(db, comic, sources=["loja_a"])
    db.refresh(comic)

    assert len(comic.offers) == 1  # nao virou duas ofertas do mesmo vendedor
    assert comic.offers[0].price == 49.90

