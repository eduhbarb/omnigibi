"""Testes de exportacao/importacao da prateleira (merge)."""

from __future__ import annotations


def test_export_tem_formato_e_quadrinhos(client):
    client.post("/api/seed")
    export = client.get("/api/export").json()

    assert export["format"] == "banca-quadrinhos.export"
    assert export["schema_version"] == 1
    assert isinstance(export["comics"], list) and export["comics"]
    first = export["comics"][0]
    assert "title" in first and "history" in first


def test_import_do_proprio_export_e_idempotente(client):
    client.post("/api/seed")
    export = client.get("/api/export").json()
    antes = len(client.get("/api/comics").json())

    response = client.post("/api/import", json=export)
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["comics_created"] == 0
    assert result["history_added"] == 0
    assert len(client.get("/api/comics").json()) == antes


def test_import_cria_quadrinho_e_deduplica_historico(client):
    payload = {
        "format": "banca-quadrinhos.export",
        "schema_version": 1,
        "comics": [
            {
                "title": "Sandman Vol. 1",
                "publisher": "Panini",
                "history": [
                    {
                        "source": None,
                        "price": 59.9,
                        "change_type": "inicial",
                        "recorded_at": "2026-01-01T10:00:00+00:00",
                    },
                    {
                        "source": None,
                        "price": 49.9,
                        "change_type": "queda",
                        "recorded_at": "2026-01-02T10:00:00+00:00",
                    },
                ],
            }
        ],
    }

    first = client.post("/api/import", json=payload).json()["result"]
    assert first["comics_created"] == 1
    assert first["history_added"] == 2

    titles = [c["title"] for c in client.get("/api/comics").json()]
    assert "Sandman Vol. 1" in titles

    again = client.post("/api/import", json=payload).json()["result"]
    assert again["comics_created"] == 0
    assert again["history_added"] == 0
    assert again["history_skipped"] == 2


def test_import_rejeita_formato_desconhecido(client):
    response = client.post("/api/import", json={"format": "nada", "comics": []})
    assert response.status_code == 400


def test_import_rejeita_versao_mais_nova(client):
    response = client.post(
        "/api/import",
        json={"format": "banca-quadrinhos.export", "schema_version": 999, "comics": []},
    )
    assert response.status_code == 400
