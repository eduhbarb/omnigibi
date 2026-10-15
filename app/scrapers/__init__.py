"""Registro de coletores disponiveis."""

from __future__ import annotations

from app.config import Settings, settings as default_settings
from app.scrapers.amazon import AmazonScraper
from app.scrapers.base import (  # noqa: F401
    BaseScraper,
    OfferResult,
    ScraperError,
    ScraperReport,
)
from app.scrapers.generic_html import build_site_scrapers, load_site_configs
from app.scrapers.mercadolivre import MercadoLivreScraper
from app.scrapers.panini import PaniniScraper
from app.scrapers.simulator import SimuladorScraper


def _make(source: str, settings: Settings) -> BaseScraper | None:
    """Instancia um coletor pelo nome."""
    settings = settings or default_settings
    if source == "mercadolivre":
        return MercadoLivreScraper(settings)
    if source == "amazon":
        return AmazonScraper(settings)
    if source == "panini":
        return PaniniScraper(settings)
    if source == "simulador":
        return SimuladorScraper(settings)

    for scraper in build_site_scrapers(settings, only=[source]):
        return scraper
    return None


def build_scrapers(
    settings: Settings | None = None,
    sources: list[str] | None = None,
) -> list[BaseScraper]:
    """Monta a lista de coletores habilitados, na ordem configurada."""
    settings = settings or default_settings
    names = sources if sources is not None else settings.enabled_sources

    scrapers: list[BaseScraper] = []
    seen: set[str] = set()
    for name in names:
        key = (name or "").strip().lower()
        if not key or key in seen:
            continue
        scraper = _make(key, settings)
        if scraper is None:
            continue
        seen.add(key)
        scrapers.append(scraper)
    return scrapers


def available_sources(settings: Settings | None = None) -> list[dict[str, object]]:
    """Lista as fontes conhecidas e se estao ativas (para a interface)."""
    settings = settings or default_settings
    catalog: list[tuple[str, str, str, bool]] = [
        ("mercadolivre", "Mercado Livre", "https://www.mercadolivre.com.br", True),
        ("amazon", "Amazon", "https://www.amazon.com.br", True),
        ("panini", "Panini Store", "https://panini.com.br", True),
    ]
    for name, config in load_site_configs().items():
        catalog.append(
            (name, str(config.get("label", name)), str(config.get("homepage", "")), True)
        )

    enabled = set(settings.enabled_sources)
    return [
        {
            "name": name,
            "label": label,
            "homepage": homepage,
            "requires_network": requires_network,
            "enabled": name in enabled,
        }
        for name, label, homepage, requires_network in catalog
    ]


__all__ = [
    "BaseScraper",
    "OfferResult",
    "ScraperError",
    "ScraperReport",
    "AmazonScraper",
    "MercadoLivreScraper",
    "PaniniScraper",
    "SimuladorScraper",
    "available_sources",
    "build_scrapers",
    "load_site_configs",
]
