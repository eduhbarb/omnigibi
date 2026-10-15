"""Servicos de dominio da aplicacao."""

from app.services.text import (  # noqa: F401
    base_title,
    expand_query_variants,
    extract_volume,
    guess_issue,
    normalize,
    parse_price,
    parse_price_range,
    relevance,
    same_product,
    similarity,
    strip_accents,
    volumes_compatible,
)
