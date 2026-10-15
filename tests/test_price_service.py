"""Testes da regra central: historico gravado apenas em mudancas de preco."""

from __future__ import annotations

from app.scrapers.base import OfferResult
from app.services.price_service import (
    comic_alerts,
    comic_snapshot,
    create_comic,
    find_comic,
    sync_offers,
)


def _offer(source: str, price: float, *, list_price: float | None = None, url: str = "http://loja/x"):
    return OfferResult(
        source=source,
        source_label=source.upper(),
        title="Watchmen - Edicao Definitiva",
        price=price,
        list_price=list_price,
        url=url,
        external_id=f"{source}-1",
    )


def test_primeira_coleta_gera_registro_inicial_por_loja_e_consolidado(db):
    comic = create_comic(db, "Watchmen", reference_price=100.0)

    summary = sync_offers(
        db,
        comic,
        [_offer("loja_a", 80.0, list_price=100.0), _offer("loja_b", 90.0)],
    )
    db.commit()

    assert summary["created_offers"] == 2
    assert summary["history_entries"] == 3  # duas lojas + 1 consolidado
    for entry in summary["changes"]:
        assert entry["change_type"] == "inicial"
        assert entry["delta"] is None
        assert entry["discount_pct"] == 20.0 if entry["price"] == 80.0 else True

    snapshot = comic_snapshot(db, comic)
    assert snapshot["current_price"] == 80.0
    assert snapshot["current_source"] == "loja_a"
    assert snapshot["lowest_ever"] == 80.0
    assert snapshot["highest_ever"] == 90.0
    assert snapshot["discount_pct"] == 20.0
    assert snapshot["is_lowest_ever"] is True


def test_sem_mudanca_de_preco_nao_gera_registro(db):
    comic = create_comic(db, "Watchmen", reference_price=100.0)
    sync_offers(db, comic, [_offer("loja_a", 80.0)])
    db.commit()

    summary = sync_offers(db, comic, [_offer("loja_a", 80.0)])
    db.commit()

    assert summary["history_entries"] == 0
    assert summary["updated_offers"] == 1


def test_queda_de_preco_registra_delta_negativo_e_desconto(db):
    comic = create_comic(db, "Watchmen", reference_price=100.0)
    sync_offers(db, comic, [_offer("loja_a", 80.0, list_price=100.0)])
    db.commit()

    summary = sync_offers(db, comic, [_offer("loja_a", 60.0, list_price=100.0)])
    db.commit()

    drops = [entry for entry in summary["changes"] if entry["change_type"] == "queda"]
    assert drops, "a queda de preco deveria ter sido registrada"

    entry = drops[0]
    assert entry["previous_price"] == 80.0
    assert entry["price"] == 60.0
    assert entry["delta"] == -20.0
    assert entry["delta_pct"] == -25.0
    assert entry["discount_pct"] == 40.0
    assert entry["lowest_ever"] == 60.0

    snapshot = comic_snapshot(db, comic)
    assert snapshot["current_price"] == 60.0
    assert snapshot["lowest_ever"] == 60.0
    assert snapshot["savings_from_highest"] == 20.0


def test_alta_de_preco_registra_delta_positivo(db):
    comic = create_comic(db, "Watchmen", reference_price=100.0)
    sync_offers(db, comic, [_offer("loja_a", 70.0)])
    db.commit()

    summary = sync_offers(db, comic, [_offer("loja_a", 95.0)])
    db.commit()

    rises = [entry for entry in summary["changes"] if entry["change_type"] == "alta"]
    assert rises
    assert rises[0]["delta"] == 25.0
    assert rises[0]["delta_pct"] == 35.71

    snapshot = comic_snapshot(db, comic)
    assert snapshot["lowest_ever"] == 70.0  # o menor historico nao se perde
    assert snapshot["is_lowest_ever"] is False


def test_menor_preco_historico_e_preservado_entre_lojas(db):
    comic = create_comic(db, "Watchmen", reference_price=100.0)
    sync_offers(db, comic, [_offer("loja_a", 85.0), _offer("loja_b", 95.0)])
    db.commit()

    # loja_a sobe; o menor preco (da propria historico) continua sendo 85
    sync_offers(db, comic, [_offer("loja_a", 110.0), _offer("loja_b", 95.0)])
    db.commit()

    snapshot = comic_snapshot(db, comic)
    assert snapshot["current_price"] == 95.0
    assert snapshot["lowest_ever"] == 85.0
    assert snapshot["is_lowest_ever"] is False


def test_find_comic_ignora_acentos_e_pontuacao(db):
    create_comic(db, "V de Vingança")
    db.commit()
    assert find_comic(db, "v de vinganca") is not None
    assert find_comic(db, "Sandman") is None


def _offer_estoque(source: str, *, price: float | None, in_stock: bool) -> OfferResult:
    return OfferResult(
        source=source,
        source_label=source.upper(),
        title="Absolute Batman 01",
        price=price,
        url=f"http://loja/{source}",
        external_id=f"{source}-1",
        in_stock=in_stock,
    )


def test_oferta_esgotada_persiste_sem_preco_e_sem_historico(db):
    comic = create_comic(db, "Absolute Batman 01")

    summary = sync_offers(db, comic, [_offer_estoque("amazon", price=None, in_stock=False)])
    db.commit()

    assert summary["created_offers"] == 1
    assert summary["history_entries"] == 0  # esgotado nao gera historico

    oferta = comic.offers[0]
    assert oferta.price is None
    assert oferta.in_stock is False

    snapshot = comic_snapshot(db, comic)
    assert snapshot["current_price"] is None
    assert snapshot["in_stock"] is False
    assert snapshot["out_of_stock_count"] == 1


def test_volta_ao_estoque_gera_aviso_quando_monitorado(db):
    comic = create_comic(db, "Absolute Batman 01")
    comic.tracked = True
    db.commit()

    sync_offers(db, comic, [_offer_estoque("amazon", price=None, in_stock=False)])
    db.commit()
    assert comic_alerts(db) == []

    # a loja volta a ter estoque -> aviso registrado
    sync_offers(db, comic, [_offer_estoque("amazon", price=39.9, in_stock=True)])
    db.commit()

    alertas = comic_alerts(db, only_unseen=True)
    assert len(alertas) == 1
    alerta = alertas[0]
    assert alerta.kind == "volta_estoque"
    assert alerta.comic_id == comic.id
    assert alerta.price == 39.9
    assert "voltou ao estoque" in alerta.message

    # sem nova transicao, nao duplica o aviso
    sync_offers(db, comic, [_offer_estoque("amazon", price=39.9, in_stock=True)])
    db.commit()
    assert len(comic_alerts(db)) == 1


def test_oferta_em_estoque_sem_preco_e_invalida():
    import pytest

    from app.scrapers.base import ScraperError

    with pytest.raises(ScraperError):
        _offer_estoque("amazon", price=None, in_stock=True)
