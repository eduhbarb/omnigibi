"""Modelos ORM: quadrinho da banca, ofertas por loja e historico de precos."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    """Agora em UTC (aware)."""
    return datetime.now(timezone.utc)


class Comic(Base):
    """Um quadrinho na banca virtual."""

    __tablename__ = "comics"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    publisher: Mapped[str | None] = mapped_column(String(120))
    issue: Mapped[str | None] = mapped_column(String(60))
    volume: Mapped[str | None] = mapped_column(String(60))
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(1000))

    # Preco de referencia (capa / preco "de"). Base para o calculo de desconto.
    reference_price: Mapped[float | None] = mapped_column(Float)
    # Preco alvo: dispara alerta quando alguma oferta ficar igual ou abaixo.
    target_price: Mapped[float | None] = mapped_column(Float)

    tags: Mapped[str | None] = mapped_column(String(300))
    tracked: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    offers: Mapped[list["Offer"]] = relationship(
        back_populates="comic", cascade="all, delete-orphan", lazy="selectin"
    )
    price_history: Mapped[list["PriceHistory"]] = relationship(
        back_populates="comic",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PriceHistory.recorded_at.desc()",
        foreign_keys="PriceHistory.comic_id",
    )

    __table_args__ = (
        UniqueConstraint("normalized_title", name="uq_comics_normalized_title"),
        Index("ix_comics_tracked", "tracked"),
    )

    def __repr__(self) -> str:  # pragma: no cover - depuracao
        return f"<Comic {self.id} {self.title!r}>"


class Offer(Base):
    """Uma listagem de um quadrinho em uma loja especifica."""

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    comic_id: Mapped[int] = mapped_column(ForeignKey("comics.id", ondelete="CASCADE"), index=True)

    source: Mapped[str] = mapped_column(String(60), nullable=False)
    source_label: Mapped[str | None] = mapped_column(String(120))
    external_id: Mapped[str] = mapped_column(String(300), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(400), nullable=False)
    url: Mapped[str] = mapped_column(String(1200), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1200))
    seller: Mapped[str | None] = mapped_column(String(200))
    condition: Mapped[str | None] = mapped_column(String(60))

    # Preco pode ser nulo quando a loja lista o produto SEM preco (esgotado).
    # Nesse caso a oferta entra na banca marcada como indisponivel em vez de
    # ser descartada, e o produto nunca some da prateleira.
    price: Mapped[float | None] = mapped_column(Float)
    list_price: Mapped[float | None] = mapped_column(Float)
    shipping: Mapped[float | None] = mapped_column(Float)
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Produto em pre-venda (ainda nao lancado): compravel, mas sinalizado.
    # server_default garante que a migracao de bancos antigos preencha as linhas.
    preorder: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    # Quando o estoque mudou pela ultima vez (entrou/saiu de estoque).
    stock_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Preco anterior da propria oferta (usado no diff instantaneo exibido na UI).
    previous_price: Mapped[float | None] = mapped_column(Float)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    comic: Mapped[Comic] = relationship(back_populates="offers")

    __table_args__ = (
        UniqueConstraint("comic_id", "source", "external_id", name="uq_offer_identity"),
    )

    def __repr__(self) -> str:  # pragma: no cover - depuracao
        return f"<Offer {self.source} {self.price}>"


class PriceHistory(Base):
    """Registro historico: gravado sempre que o preco de um quadrinho muda.

    ``source`` nulo representa o preco consolidado (melhor preco entre todas as
    lojas). ``source`` preenchido representa a evolucao daquela loja.
    """

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    comic_id: Mapped[int] = mapped_column(ForeignKey("comics.id", ondelete="CASCADE"), index=True)
    source: Mapped[str | None] = mapped_column(String(60))

    price: Mapped[float] = mapped_column(Float, nullable=False)
    previous_price: Mapped[float | None] = mapped_column(Float)
    list_price: Mapped[float | None] = mapped_column(Float)

    # Variacao absoluta (negativa = caiu) e relativa (%).
    delta: Mapped[float | None] = mapped_column(Float)
    delta_pct: Mapped[float | None] = mapped_column(Float)
    # Desconto em relacao ao preco de referencia/capa no momento do registro.
    discount_pct: Mapped[float | None] = mapped_column(Float)
    # Menor preco ja visto para o quadrinho ate este registro.
    lowest_ever: Mapped[float | None] = mapped_column(Float)

    change_type: Mapped[str] = mapped_column(String(20), default="inicial", nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    comic: Mapped[Comic] = relationship(back_populates="price_history", foreign_keys=[comic_id])

    __table_args__ = (
        Index("ix_history_comic_source_time", "comic_id", "source", "recorded_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - depuracao
        return f"<PriceHistory comic={self.comic_id} {self.price} {self.change_type}>"


class Alert(Base):
    """Aviso gerado pela monitoracao (ex.: produto voltou ao estoque).

    Fica visivel na interface ate o usuario marca-lo como lido.
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    comic_id: Mapped[int] = mapped_column(ForeignKey("comics.id", ondelete="CASCADE"), index=True)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"))

    # tipo do aviso: "volta_estoque" (por enquanto o unico automatico).
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(String(400), nullable=False)

    source: Mapped[str | None] = mapped_column(String(60))
    source_label: Mapped[str | None] = mapped_column(String(120))
    seller: Mapped[str | None] = mapped_column(String(200))
    price: Mapped[float | None] = mapped_column(Float)
    url: Mapped[str | None] = mapped_column(String(1200))

    seen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - depuracao
        return f"<Alert {self.kind} comic={self.comic_id} seen={self.seen}>"
