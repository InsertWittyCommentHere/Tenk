"""Configuration: platform settings + per-company config.

A *company* is data, not code (DESIGN.md §2.2): ticker, CIK, exchange, currency,
fiscal-year-end, peer set, and optional concept overrides. Adding a new company
is dropping a YAML file in `config/companies/`. ACN is the seeded first row.

Platform settings (the SEC User-Agent, paths) come from env / a `.env` file via
pydantic-settings, so secrets and machine-specific paths stay out of the repo.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = two levels up from this file (src/equity_research/config.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
COMPANIES_DIR = REPO_ROOT / "config" / "companies"
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    """Platform-wide settings, overridable via env vars (prefix EQR_) or .env."""

    model_config = SettingsConfigDict(
        env_prefix="EQR_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # SEC requires a descriptive UA with contact email. Override via EQR_SEC_USER_AGENT.
    sec_user_agent: str = Field(
        default="Equity Research (personal) rjain1@imsa.edu",
        description="User-Agent sent to SEC EDGAR; must include a contact email.",
    )
    data_dir: Path = DATA_DIR
    db_path: Path = DATA_DIR / "equity_research.db"
    chunks_db_path: Path = DATA_DIR / "chunks.db"
    alerts_db_path: Path = DATA_DIR / "alerts.db"
    cache_dir: Path = DATA_DIR / "cache" / "edgar"
    sec_max_rps: float = 8.0


class CompanyConfig(BaseModel):
    """Per-company config — the unit of multi-company extensibility."""

    ticker: str
    cik: int
    name: str
    exchange: str | None = None
    currency: str = "USD"
    fiscal_year_end: str | None = Field(default=None, description="MMDD, e.g. '0831'.")
    peers: list[str] = Field(default_factory=list, description="Peer tickers for comps.")
    concept_overrides: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Canonical-metric -> XBRL concepts, to override defaults for odd filers.",
    )
    notes: str | None = None


def load_company(ticker_or_path: str) -> CompanyConfig:
    """Load a company config by ticker (looks in config/companies/<ticker>.yaml) or path."""
    p = Path(ticker_or_path)
    if not p.exists():
        p = COMPANIES_DIR / f"{ticker_or_path.lower()}.yaml"
    if not p.exists():
        raise FileNotFoundError(
            f"No company config for {ticker_or_path!r} (looked for {p}). "
            f"Add one under {COMPANIES_DIR}."
        )
    data = yaml.safe_load(p.read_text())
    return CompanyConfig(**data)


def list_companies() -> list[str]:
    if not COMPANIES_DIR.exists():
        return []
    return sorted(p.stem for p in COMPANIES_DIR.glob("*.yaml"))


settings = Settings()
