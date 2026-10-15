"""Configuracao central da aplicacao, carregada de variaveis de ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "sim"}


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def _as_list(value: str | None, default: tuple[str, ...]) -> list[str]:
    if not value or not value.strip():
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _default_data_dir() -> Path:
    """Pasta de dados local.

    Segue o diretorio do arquivo SQLite quando ``DATABASE_URL`` for sqlite (util
    em ambientes serverless, onde so ``/tmp`` e gravavel). Caso contrario usa
    ``BASE_DIR/data``.
    """
    url = os.getenv("DATABASE_URL") or ""
    if url.startswith("sqlite"):
        raw = url.split("///", 1)[-1]
        if raw and raw not in {":memory:", ""}:
            try:
                return Path(raw).expanduser().resolve().parent
            except OSError:  # pragma: no cover - caminho invalido no sistema
                pass
    return BASE_DIR / "data"


@dataclass(frozen=True)
class Settings:
    """Configuracao imutavel da aplicacao."""

    app_name: str = "Omnigibi"
    version: str = "1.4.0"
    currency: str = "BRL"
    currency_symbol: str = "R$"

    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL")
        or f"sqlite:///{(BASE_DIR / 'data' / 'banca.db').as_posix()}"
    )

    web_dir: Path = field(default_factory=lambda: BASE_DIR / "web")
    data_dir: Path = field(default_factory=_default_data_dir)

    enabled_sources: list[str] = field(
        default_factory=lambda: _as_list(
            os.getenv("ENABLED_SOURCES"), ("mercadolivre", "amazon", "panini")
        )
    )

    http_timeout: float = field(default_factory=lambda: _as_float(os.getenv("HTTP_TIMEOUT"), 12.0))
    max_results_per_source: int = field(
        default_factory=lambda: _as_int(os.getenv("MAX_RESULTS_PER_SOURCE"), 12)
    )
    # User-Agent usado nos coletores. Sobrescreva via USER_AGENT no .env.
    # Alguns sites (ex.: Amazon) so entregam o HTML de busca para navegadores
    # comuns, por isso o padrao e um UA de navegador real.
    user_agent: str = field(
        default_factory=lambda: os.getenv("USER_AGENT")
        or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    )

    scheduler_enabled: bool = field(
        default_factory=lambda: _as_bool(os.getenv("SCHEDULER_ENABLED"), True)
    )
    scrape_interval_minutes: int = field(
        default_factory=lambda: _as_int(os.getenv("SCRAPE_INTERVAL_MINUTES"), 30)
    )

    seed_demo_data: bool = field(default_factory=lambda: _as_bool(os.getenv("SEED_DEMO_DATA"), True))

    # Uma oferta so entra como "relevante" se a similaridade com a busca passar disto.
    relevance_threshold: float = 0.34
    # Abaixo disto a oferta e descartada; entre os dois limites entra em "relacionados".
    related_threshold: float = 0.2

    def ensure_dirs(self) -> None:
        """Garante a pasta de dados quando o sistema de arquivos permite.

        Em runtimes serverless (Vercel) o codigo roda em um FS somente leitura,
        exceto ``/tmp``; nesse caso a criacao falha e deve ser ignorada, pois o
        banco aponta para um caminho gravavel.
        """
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


settings = Settings()
settings.ensure_dirs()
