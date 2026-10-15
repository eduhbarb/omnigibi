"""Fonte simulada: gera ofertas deterministicas para demonstracao/offline.

Util para rodar a banca sem internet (ou quando as lojas bloqueiam o robô):
os precos variam de forma suave ao longo do tempo, o que produz um historico
realista de quedas e altas.
"""

from __future__ import annotations

import hashlib
import math
import os
import time
from typing import Any

from app.scrapers.base import BaseScraper, OfferResult


def _bucket_seconds() -> int:
    try:
        return max(30, int(os.getenv("SIM_PRICE_BUCKET_SECONDS", "120")))
    except ValueError:
        return 120


# Catalogo de referencia: (titulo, editora, preco de capa, preco base de mercado)
CATALOG: list[tuple[str, str, float, float]] = [
    ("Watchmen - Edicao Definitiva", "Panini", 249.90, 179.90),
    ("Batman: O Cavaleiro das Trevas Retorna", "Panini", 129.90, 94.90),
    ("Sandman - Edicao Definitiva Vol. 1", "Panini", 189.90, 139.90),
    ("V de Vinganca", "Panini", 89.90, 64.90),
    ("Maus - A Historia de um Sobrevivente", "Companhia das Letras", 79.90, 55.90),
    ("Persepolis", "Companhia das Letras", 99.90, 68.00),
    ("O Caydeiro de Cletus", "Devir", 59.90, 41.90),
    ("Turma da Monica - Colecao Historica Vol. 2", "Panini", 74.90, 49.90),
    ("Homem-Aranha: A Ultima Cacada de Kraven", "Panini", 114.90, 82.90),
    ("X-Men: Dias de um Futuro Esquecido", "Panini", 109.90, 78.90),
    ("A Liga Extraordinaria Vol. 1", "Panini", 129.90, 88.90),
    ("Preacher - Edicao Definitiva Vol. 1", "Panini", 159.90, 118.90),
    ("Bone - Edicao Completa", "Panini", 299.90, 229.90),
    ("Scott Pilgrim - Colecao Completa", "Devir", 199.90, 149.90),
    ("Novo X-Men: E de Extincao", "Panini", 64.90, 44.90),
]

# (id, rotulo, fator de preco, margem de oscilacao)
STORES: list[tuple[str, str, float, float]] = [
    ("geek_store", "Geek Store BR", 1.00, 0.10),
    ("sebo_heroi", "Sebo do Heroi", 0.86, 0.14),
    ("livraria_hq", "Livraria HQ", 1.09, 0.08),
    ("mega_comics", "Mega Comics", 0.94, 0.18),
]

_INDEX = {}


def _norm(text: str) -> str:
    from app.services.text import normalize

    return normalize(text)


for _title, _publisher, _cover, _base in CATALOG:
    _INDEX[_norm(_title)] = (_title, _publisher, _cover, _base)


def _seed(text: str) -> float:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _round_price(value: float) -> float:
    """Arredonda para a dezena de centavos (padrao de preco de varejo)."""
    return round(round(value * 10) / 10, 2)


class SimuladorScraper(BaseScraper):
    name = "simulador"
    label = "Simulador (offline)"
    homepage = ""
    requires_network = False

    def resolve_product(self, query: str) -> tuple[str, str, float, float]:
        """Acha o item do catalogo ou sintetiza um a partir da busca."""
        normalized = _norm(query)
        if normalized in _INDEX:
            return _INDEX[normalized]

        # Busca parcial com melhor sobreposicao de tokens.
        query_tokens = set(normalized.split())
        best: tuple[str, str, float, float] | None = None
        best_score = 0.0
        for key, entry in _INDEX.items():
            tokens = set(key.split())
            if not tokens:
                continue
            score = len(query_tokens & tokens) / len(tokens | query_tokens)
            if score > best_score:
                best, best_score = entry, score
        if best is not None and best_score >= 0.5:
            return best

        score = _seed(normalized)
        base = round(29.90 + score * 190, 2)
        cover = round(base * 1.38, 2)
        return query.strip().title(), "Editora Independente", cover, base

    def _drift(self, key: str, amplitude: float, bucket: int) -> float:
        seed = _seed(key) * 12.0
        slow = math.sin((bucket / 9.0) + seed)
        fast = math.sin((bucket / 3.7) + seed * 2.1)
        drift = (slow * 0.7 + fast * 0.3) * amplitude
        # Eventual "promocao relampago" que derruba bastante o preco.
        promo = _seed(f"{key}:{bucket // 3}")
        if promo > 0.93:
            drift -= amplitude * 1.9
        return drift

    def search(self, query: str, limit: int | None = None) -> list[OfferResult]:
        limit = limit or self.settings.max_results_per_source
        title, publisher, cover_price, base_price = self.resolve_product(query)

        bucket = int(time.time() // _bucket_seconds())
        offers: list[OfferResult] = []

        for store_id, store_label, factor, amplitude in STORES[:limit]:
            key = f"{_norm(title)}|{store_id}"
            price = base_price * factor * (1 + self._drift(key, amplitude, bucket))
            price = _round_price(max(price, base_price * 0.35))

            list_price = cover_price
            # Algumas lojas usam o proprio preco base como "preco de" (promocao).
            if _seed(f"{key}:list") > 0.6:
                list_price = _round_price(base_price * 1.15)
            if list_price <= price:
                list_price = None

            stock_score = _seed(f"{key}:stock")
            in_stock = stock_score > 0.08

            external_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
            offers.append(
                OfferResult(
                    source=self.name,
                    source_label=f"{store_label} (sim)",
                    title=f"{title} - {publisher}",
                    price=price,
                    list_price=list_price,
                    url=f"https://simulador.local/{store_id}/{external_id}",
                    external_id=f"{store_id}:{external_id}",
                    seller=store_label,
                    condition="novo",
                    shipping=0.0 if _seed(f"{key}:ship") > 0.5 else _round_price(9.9 + _seed(f"{key}:shipv") * 15),
                    in_stock=in_stock,
                    raw={"simulado": True, "store": store_id, "publisher": publisher},
                )
            )

        return offers

    @staticmethod
    def catalog() -> list[dict[str, Any]]:
        """Catalogo de demonstracao (usado para popular a banca)."""
        return [
            {
                "title": title,
                "publisher": publisher,
                "reference_price": cover,
                "base_price": base,
            }
            for title, publisher, cover, base in CATALOG
        ]
