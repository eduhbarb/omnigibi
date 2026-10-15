"""Contrato base dos coletores (scrapers) de preco."""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings
from app.services.text import normalize, parse_price, parse_price_range


class ScraperError(RuntimeError):
    """Falha controlada de um coletor (site fora do ar, bloqueio, layout mudou...)."""


@dataclass(slots=True)
class OfferResult:
    """Oferta normalizada retornada por qualquer coletor."""

    source: str
    source_label: str
    title: str
    price: float | None
    url: str
    external_id: str = ""
    list_price: float | None = None
    image_url: str | None = None
    seller: str | None = None
    condition: str | None = "novo"
    shipping: float | None = None
    in_stock: bool = True
    # Produto em pre-venda (ainda nao lancado): continua compravel, mas e
    # sinalizado na interface em vez de se passar por uma edicao ja disponivel.
    preorder: bool = False
    relevance: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.external_id:
            basis = f"{self.source}|{self.url}|{self.title}"
            self.external_id = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]
        # O preco so e obrigatorio quando ha estoque. Produto esgotado pode vir
        # sem preco e ainda assim ser util (aparece marcado como "em falta").
        if self.in_stock and (self.price is None or self.price <= 0):
            raise ScraperError("Oferta em estoque sem preco valido")
        if self.price is not None and self.price <= 0:
            self.price = None
        if self.list_price is not None and (self.price is None or self.list_price <= self.price):
            # Preco "de" menor ou igual ao "por" e ruido de layout.
            self.list_price = None

    @property
    def discount_pct(self) -> float:
        if self.price is None or not self.list_price or self.list_price <= self.price:
            return 0.0
        return round((self.list_price - self.price) / self.list_price * 100, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_label": self.source_label,
            "title": self.title,
            "price": self.price,
            "list_price": self.list_price,
            "discount_pct": self.discount_pct,
            "url": self.url,
            "external_id": self.external_id,
            "image_url": self.image_url,
            "seller": self.seller,
            "condition": self.condition,
            "shipping": self.shipping,
            "in_stock": self.in_stock,
            "preorder": self.preorder,
            "relevance": self.relevance,
        }


@dataclass(slots=True)
class ScraperReport:
    """Resultado da execucao de um coletor, com ou sem erro."""

    source: str
    label: str
    offers: list[OfferResult] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "label": self.label,
            "count": len(self.offers),
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


class BaseScraper(ABC):
    """Classe base para todos os coletores.

    Subclasses implementam :meth:`search` e devolvem :class:`OfferResult`.
    """

    name: str = "base"
    label: str = "Base"
    homepage: str = ""
    requires_network: bool = True

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client

    # ------------------------------------------------------------------ HTTP
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.settings.http_timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": self.settings.user_agent,
                    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                },
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # -------------------------------------------------------------- contrato
    @abstractmethod
    def search(self, query: str, limit: int | None = None) -> list[OfferResult]:
        """Busca ofertas para ``query``. Pode levantar :class:`ScraperError`."""

    def safe_search(self, query: str, limit: int | None = None) -> ScraperReport:
        """Executa :meth:`search` capturando qualquer falha."""
        started = time.perf_counter()
        report = ScraperReport(source=self.name, label=self.label)
        try:
            offers = self.search(query, limit=limit) or []
            # Mantem ofertas em estoque com preco valido E ofertas esgotadas
            # (que podem vir sem preco) — produto esgotado nao e descartado.
            report.offers = [
                offer for offer in offers if (not offer.in_stock) or (offer.price and offer.price > 0)
            ]
        except ScraperError as exc:
            report.error = str(exc)
        except httpx.HTTPError as exc:
            report.error = f"falha de rede: {type(exc).__name__}"
        except Exception as exc:  # pragma: no cover - protecao defensiva
            report.error = f"erro inesperado: {exc}"
        report.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return report

    # -------------------------------------------------------------- helpers
    @staticmethod
    def parse_money(raw: Any) -> float | None:
        return parse_price(raw)

    @staticmethod
    def parse_money_range(raw: str) -> tuple[float | None, float | None]:
        return parse_price_range(raw)

    @staticmethod
    def normalize(text: str) -> str:
        return normalize(text)

    def fetch(self, url: str, *, retries: int = 0, retry_delay: float = 0.9) -> httpx.Response:
        """GET com tratamento de erro padronizado.

        ``retries`` repete a requisicao em bloqueios transitorios (429/503),
        comuns em sites com protecao anti-robô.
        """
        attempt = 0
        while True:
            try:
                response = self.client.get(url)
            except httpx.HTTPError as exc:
                raise ScraperError(f"falha de rede em {url}: {type(exc).__name__}") from exc

            transient = response.status_code in (429, 503)
            if transient and attempt < retries:
                attempt += 1
                time.sleep(retry_delay * attempt)
                continue

            if response.status_code == 403:
                raise ScraperError("acesso bloqueado pelo site (HTTP 403)")
            if response.status_code == 429:
                raise ScraperError("limite de requisicoes atingido (HTTP 429)")
            if response.status_code == 503:
                raise ScraperError("site indisponivel no momento (HTTP 503)")
            if response.status_code >= 400:
                raise ScraperError(f"resposta HTTP {response.status_code}")
            return response
