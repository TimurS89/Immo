"""Configuration loader for Immo property search.

Country-keyed structure: ``AppConfig.search_areas`` maps a country code
("DE", "FR", "LU", ...) to a :class:`CountryConfig`. Each ``CountryConfig``
carries its own ``enabled`` flag, ``currency``, ``locale``/``timezone`` (used by
the browser), search ``areas``, and the ``portals`` (scrapers) for that country.

Adding a country/portal is purely a config change — no consumer code edits, and
no DB enum to migrate (see ``src/database/models.py``). This map is the dynamic
"source registry" that replaces the old hardcoded ``source_enum``/``country_enum``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


def _resolve_env_vars(value: str) -> str:
    """Replace ${VAR} placeholders with environment variable values."""
    missing: list[str] = []

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        val = os.environ.get(var_name)
        if val is None:
            missing.append(var_name)
            return ""
        return val

    result = re.sub(r"\$\{(\w+)}", replacer, value)
    if missing:
        import logging
        logging.getLogger(__name__).warning(
            f"Missing environment variables: {', '.join(missing)}"
        )
    return result


def _resolve_dict(d: dict) -> dict:
    """Recursively resolve env vars in a dict."""
    result = {}
    for k, v in d.items():
        if isinstance(v, str):
            result[k] = _resolve_env_vars(v)
        elif isinstance(v, dict):
            result[k] = _resolve_dict(v)
        elif isinstance(v, list):
            result[k] = [_resolve_dict(i) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result


class SearchArea(BaseModel):
    name: str
    city: str | None = None
    postal_codes: list[str] = Field(default_factory=list)
    radius_km: int = 0
    departments: list[str] = Field(default_factory=list)  # FR
    # Generic region identifiers (cantons/communes/zones) for other markets (LU).
    regions: list[str] = Field(default_factory=list)


class Filters(BaseModel):
    min_price: int = 0
    max_price: int = 1_000_000
    min_rooms: int = 3
    min_area_sqm: int = 50
    property_types: list[str] = Field(default_factory=lambda: ["apartment", "house", "land"])


class PortalConfig(BaseModel):
    """A single scraper/source within a country."""

    name: str
    enabled: bool = True
    base_url: str | None = None


class CountryConfig(BaseModel):
    """All settings scoped to one country code."""

    enabled: bool = False
    currency: str = "EUR"
    locale: str = "en-US"
    timezone: str = "UTC"
    regions: list[str] = Field(default_factory=list)
    areas: list[SearchArea] = Field(default_factory=list)
    portals: list[PortalConfig] = Field(default_factory=list)

    def enabled_portals(self) -> list[str]:
        """Names of enabled portals (only meaningful when the country is enabled)."""
        return [p.name for p in self.portals if p.enabled]


class ScrapersConfig(BaseModel):
    # NOTE: per-country/source enablement now lives in CountryConfig.portals.
    request_delay_seconds: list[int] = Field(default_factory=lambda: [3, 8])
    # High enough to fully paginate the largest commune (Luxembourg City has
    # ~4k rentals ≈ 210 pages). Small communes stop early at their own total, so
    # this only deepens the big ones. See athome scraper's per-search ceiling.
    max_pages_per_source: int = 250
    headless: bool = True
    captcha_service: str | None = None


class EmailConfig(BaseModel):
    enabled: bool = False
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender_email: str = ""
    sender_password: str = ""
    recipient_email: str = ""


class ReportsConfig(BaseModel):
    output_dir: str = "./data/reports"
    email: EmailConfig = Field(default_factory=EmailConfig)


class ProxyConfig(BaseModel):
    enabled: bool = False
    type: str = "residential"
    file: str = "./config/proxies.txt"


class DashboardConfig(BaseModel):
    port: int = 8501
    host: str = "127.0.0.1"


class DatabaseConfig(BaseModel):
    path: str = "./data/immo.db"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "./logs/immo.log"


class AppConfig(BaseModel):
    # Country-keyed registry of search scope + portals.
    search_areas: dict[str, CountryConfig] = Field(default_factory=dict)
    filters_buy: Filters = Field(default_factory=Filters)
    filters_rent: Filters = Field(
        default_factory=lambda: Filters(max_price=2500, property_types=["apartment", "house"])
    )
    scrapers: ScrapersConfig = Field(default_factory=ScrapersConfig)
    reports: ReportsConfig = Field(default_factory=ReportsConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def country(self, code: str) -> CountryConfig | None:
        return self.search_areas.get(code)

    def enabled_countries(self) -> list[str]:
        return [code for code, cc in self.search_areas.items() if cc.enabled]


def _parse_country_config(raw: dict[str, Any]) -> CountryConfig:
    portals_raw = raw.get("portals", [])
    portals = [
        p if isinstance(p, PortalConfig)
        else (PortalConfig(**p) if isinstance(p, dict) else PortalConfig(name=str(p)))
        for p in portals_raw
    ]
    return CountryConfig(
        enabled=raw.get("enabled", False),
        currency=raw.get("currency", "EUR"),
        locale=raw.get("locale", "en-US"),
        timezone=raw.get("timezone", "UTC"),
        regions=raw.get("regions", []),
        areas=[SearchArea(**a) for a in raw.get("areas", [])],
        portals=portals,
    )


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load configuration from YAML file + environment variables."""
    load_dotenv(PROJECT_ROOT / ".env")

    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        # Fall back to example config
        example = path.parent / "config.example.yaml"
        if example.exists():
            path = example

    raw: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    raw = _resolve_dict(raw)

    search_areas = {
        code: _parse_country_config(cc or {})
        for code, cc in (raw.get("search_areas", {}) or {}).items()
    }

    # Always register Luxembourg from the canonical constants (config/luxembourg.py)
    # unless the YAML explicitly defines an "LU" entry (which then takes precedence).
    if "LU" not in search_areas:
        import sys
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        try:
            from config.luxembourg import lu_country_config
            search_areas["LU"] = lu_country_config()
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Could not load Luxembourg config from config/luxembourg.py",
                exc_info=True,
            )

    filters = raw.get("filters", {})

    return AppConfig(
        search_areas=search_areas,
        filters_buy=Filters(**filters["buy"]) if "buy" in filters else Filters(),
        filters_rent=Filters(**filters["rent"]) if "rent" in filters else Filters(max_price=2500),
        scrapers=ScrapersConfig(**raw["scrapers"]) if "scrapers" in raw else ScrapersConfig(),
        reports=ReportsConfig(**raw["reports"]) if "reports" in raw else ReportsConfig(),
        proxy=ProxyConfig(**raw["proxy"]) if "proxy" in raw else ProxyConfig(),
        dashboard=DashboardConfig(**raw["dashboard"]) if "dashboard" in raw else DashboardConfig(),
        database=DatabaseConfig(**raw["database"]) if "database" in raw else DatabaseConfig(),
        logging=LoggingConfig(**raw["logging"]) if "logging" in raw else LoggingConfig(),
    )
