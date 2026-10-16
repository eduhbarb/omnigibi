"""Testes do motor stateless: o estado vive no navegador.

O endpoint ``POST /api/state`` recebe a prateleira inteira, aplica a acao e
devolve o estado recalculado. Nada deve ficar salvo no servidor.
"""

from __future__ import annotations

OFFERS = [
    {
        "source": "simulador",
        "source_label": "Loja A",
        "title": "Watchmen - Edicao Definitiva",
        "price": 120.0,
        "list_price": 150.0,
        "url": "https://exemplo.test/watchmen",
        "external_id": "abc-1",
        "in_stock": True,
    },
    {
        "source": "simulador",
        "source_label": "Loja B",
        "title": "Watchmen - Edicao Definitiva",
        "price": 99.9,
        "list_price": 150.0,
        "url": "https://exemplo.test/watchmen-b",
        "external_id": "abc-2",
        "in_stock": True,
    },
]


def _add(client, state=None):
    return client.post(
        "/api/state",
        json={
            "action": "add",
            "state": state,
            "comic": {
                "title": "Watchmen - Edicao Definitiva",
                "publisher": "Panini",
                "offers": OFFERS,
            },
            "scrape": False,
        },
    )


def test_sync_vazio_nao_guarda_nada(client):
    body = client.post("/api/state", json={"action": "sync"}).json()
    assert body["comics"] == []
    assert body["state"]["comics"] == []
    assert body["stats"]["total_comics"] == 0


def test_add_devolve_estado_e_nao_persiste_no_servidor(client):
    view = _add(client).json()
    assert view["created"] is True
    assert len(view["comics"]) == 1
    comic = view["comics"][0]
    assert comic["title"] == "Watchmen - Edicao Definitiva"
    assert comic["key"] == comic["normalized_title"]

    # o estado devolvido e o que o navegador guarda e reenvia
    store = view["state"]
    assert len(store["comics"]) == 1
    assert len(store["comics"][0]["history"]) >= 1

    # nada ficou salvo: uma requisicao sem estado ve a banca vazia
    empty = client.post("/api/state", json={"action": "sync"}).json()
    assert empty["comics"] == []

    # reenviando o estado, a prateleira volta igual
    again = client.post("/api/state", json={"action": "sync", "state": store}).json()
    assert len(again["comics"]) == 1
    assert again["comics"][0]["key"] == comic["key"]


def test_add_duplicado_nao_duplica(client):
    first = _add(client).json()
    second = _add(client, first["state"]).json()
    assert second["created"] is False
    assert len(second["comics"]) == 1


def test_remove_e_detail(client):
    view = _add(client).json()
    key = view["comics"][0]["key"]

    detail = client.post(
        "/api/state", json={"action": "detail", "state": view["state"], "key": key}
    ).json()
    assert detail["comic"]["key"] == key
    assert detail["comic"]["history"]

    removed = client.post(
        "/api/state", json={"action": "remove", "state": view["state"], "key": key}
    ).json()
    assert removed["comics"] == []

    missing = client.post(
        "/api/state", json={"action": "detail", "state": view["state"], "key": "nao-existe"}
    )
    assert missing.status_code == 404


def test_update_preco_alvo(client):
    view = _add(client).json()
    key = view["comics"][0]["key"]
    updated = client.post(
        "/api/state",
        json={
            "action": "update",
            "state": view["state"],
            "key": key,
            "patch": {"target_price": 80},
        },
    ).json()
    assert updated["comics"][0]["snapshot"]["target_price"] == 80

    saved = updated["state"]["comics"][0]
    assert saved["target_price"] == 80


def test_refresh_devolve_estado(client):
    view = _add(client).json()
    key = view["comics"][0]["key"]
    refreshed = client.post(
        "/api/state",
        json={
            "action": "refresh",
            "state": view["state"],
            "key": key,
            "sources": ["simulador"],
        },
    ).json()
    assert refreshed["detail"]
    assert len(refreshed["comics"]) == 1
    assert len(refreshed["state"]["comics"]) == 1


def test_import_faz_merge(client):
    view = _add(client).json()
    another = {
        "format": "banca-quadrinhos.export",
        "schema_version": 1,
        "comics": [
            {
                "title": "Sandman Vol. 1",
                "publisher": "Panini",
                "history": [
                    {
                        "source": None,
                        "price": 50.0,
                        "change_type": "inicial",
                        "recorded_at": "2026-01-01T00:00:00",
                    }
                ],
            }
        ],
        "alerts": [],
    }
    merged = client.post(
        "/api/state",
        json={"action": "import", "state": view["state"], "import_state": another},
    ).json()
    titles = {comic["title"] for comic in merged["comics"]}
    assert titles == {"Watchmen - Edicao Definitiva", "Sandman Vol. 1"}
    assert merged["import_result"]["comics_created"] == 1
