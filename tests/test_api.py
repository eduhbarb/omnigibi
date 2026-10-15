"""Testes de integracao da API (com a fonte simulada)."""

from __future__ import annotations

from app.scrapers.simulator import SimuladorScraper


def test_health_e_fontes(client):
    assert client.get("/api/health").json()["status"] == "ok"

    sources = client.get("/api/sources").json()
    names = {source["name"] for source in sources}
    assert {"mercadolivre", "amazon", "panini"} <= names
    # a fonte offline (simulador) foi removida das categorias de busca
    assert "simulador" not in names


def test_fluxo_completo_adicionar_atualizar_e_historico(client):
    # 1. adiciona um quadrinho (banca nasce com o historico inicial)
    response = client.post(
        "/api/comics",
        json={
            "title": "Watchmen - Edicao Definitiva",
            "publisher": "Panini",
            "sources": ["simulador"],
            "scrape": True,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    comic_id = body["comic"]["id"]
    snapshot = body["comic"]["snapshot"]
    assert snapshot["current_price"] is not None
    assert snapshot["lowest_ever"] == snapshot["current_price"]
    assert snapshot["offer_count"] > 0
    assert snapshot["store_count"] == 4  # 4 vendedores simulados
    assert snapshot["discount_pct"] > 0

    # 2. reconsulta: registra variacoes se houver
    refreshed = client.post(f"/api/comics/{comic_id}/refresh", json={"sources": ["simulador"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["result"]["collected"] > 0

    # 3. historico consolidado com delta e desconto
    history = client.get(f"/api/comics/{comic_id}/history").json()
    assert history["total"] >= 1
    first = history["entries"][0]
    assert first["discount_pct"] is not None
    assert first["change_type"] in {"inicial", "queda", "alta", "estavel"}
    assert {"price", "previous_price", "delta", "delta_pct"} <= set(first)

    # 4. detalhe completo
    detail = client.get(f"/api/comics/{comic_id}").json()
    assert detail["title"] == "Watchmen - Edicao Definitiva"
    assert detail["offers"]
    assert detail["history"]

    # 5. exportacao CSV
    csv_response = client.get(f"/api/comics/{comic_id}/history.csv")
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers["content-type"]
    assert "desconto_pct" in csv_response.text

    # 6. remocao
    assert client.delete(f"/api/comics/{comic_id}").status_code == 200
    assert client.get(f"/api/comics/{comic_id}").status_code == 404


def test_busca_agregada_com_simulador(client):
    response = client.post("/api/search", json={"query": "Sandman", "sources": ["simulador"]})
    assert response.status_code == 200
    payload = response.json()
    assert payload["product_count"] >= 1
    product = payload["results"][0]
    assert product["offer_count"] >= 2  # varias lojas simuladas
    assert product["price_min"] <= product["price_max"]
    assert product["best"]["price"] == product["price_min"]


def test_quadrinho_duplicado_nao_e_adicionado_duas_vezes(client):
    payload = {"title": "V de Vinganca", "sources": ["simulador"], "scrape": False}
    first = client.post("/api/comics", json=payload).json()
    second = client.post("/api/comics", json=payload).json()
    assert first["created"] is True
    assert second["created"] is False
    assert first["comic"]["id"] == second["comic"]["id"]


def test_preco_alvo_e_filtro_do_menor_preco(client):
    created = client.post(
        "/api/comics",
        json={
            "title": "Bone - Edicao Completa",
            "sources": ["simulador"],
            "target_price": 9999.0,
            "scrape": True,
        },
    ).json()
    comic_id = created["comic"]["id"]
    assert created["comic"]["snapshot"]["target_reached"] is True

    listed = client.get("/api/comics", params={"only_lowest": "true"}).json()
    assert any(item["id"] == comic_id for item in listed)

    filtered = client.get("/api/comics", params={"q": "bone"}).json()
    assert len(filtered) == 1


def test_stats_e_variacoes(client):
    client.post("/api/seed")

    stats = client.get("/api/stats").json()
    assert stats["total_comics"] == len(SimuladorScraper.catalog())
    assert stats["total_offers"] > 0
    # total_history conta mudancas do melhor preco (pode ser 0 logo apos o seed,
    # que grava apenas os registros "inicial"); total_observations conta tudo.
    assert stats["total_history"] >= 0
    assert stats["total_observations"] > 0

    changes = client.get("/api/changes").json()
    assert isinstance(changes, list)


def test_endpoint_de_avisos_marca_como_lido(client, db):
    from app.models import Alert, Comic

    comic = Comic(title="Absolute Batman 01", normalized_title="absolute batman 01")
    db.add(comic)
    db.commit()
    db.refresh(comic)

    db.add(
        Alert(
            comic_id=comic.id,
            kind="volta_estoque",
            message="Absolute Batman 01 voltou ao estoque em Amazon",
            source="amazon",
            source_label="Amazon",
            price=39.9,
        )
    )
    db.commit()

    body = client.get("/api/alerts").json()
    assert body["unseen"] == 1
    assert body["alerts"][0]["kind"] == "volta_estoque"
    assert body["alerts"][0]["price"] == 39.9

    assert client.post("/api/alerts/ack", json={}).json()["marked"] == 1
    assert client.get("/api/alerts").json()["unseen"] == 0
    assert client.get("/api/alerts", params={"only_unseen": "true"}).json()["alerts"] == []
