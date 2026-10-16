"""Modelos de entrada/saida da API."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    sources: list[str] | None = None
    limit: int | None = Field(default=None, ge=1, le=50)
    # Prateleira do navegador (stateless): permite marcar o que ja esta na banca.
    state: dict | None = None

    @field_validator("query")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class OfferIn(BaseModel):
    source: str
    source_label: str | None = None
    title: str = ""
    # Preco nulo = oferta esgotada (a loja lista o produto sem preco).
    price: float | None = Field(default=None, ge=0)
    list_price: float | None = None
    url: str = ""
    external_id: str | None = None
    image_url: str | None = None
    seller: str | None = None
    condition: str | None = None
    shipping: float | None = None
    in_stock: bool = True
    preorder: bool = False


class ComicCreate(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    publisher: str | None = None
    issue: str | None = None
    volume: str | None = None
    description: str | None = None
    cover_url: str | None = None
    reference_price: float | None = Field(default=None, ge=0)
    target_price: float | None = Field(default=None, ge=0)
    tags: str | None = None
    offers: list[OfferIn] = Field(default_factory=list)
    sources: list[str] | None = None
    scrape: bool = True


class ComicUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=300)
    publisher: str | None = None
    issue: str | None = None
    volume: str | None = None
    description: str | None = None
    cover_url: str | None = None
    reference_price: float | None = Field(default=None, ge=0)
    target_price: float | None = Field(default=None, ge=0)
    tags: str | None = None
    tracked: bool | None = None


class RefreshRequest(BaseModel):
    sources: list[str] | None = None
