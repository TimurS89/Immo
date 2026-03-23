"""Configuration loader for Immo property search."""

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
    departments: list[str] = Field(default_factory=list)


class Filters(BaseModel):
    min_price: int = 0
    max_price: int = 1_000_000
    min_rooms: int = 3
    min_area_sqm: int = 50
    property_types: list[str] = Field(default_factory=lambda: ["apartment", "house", "land"])


class ScrapersConfig(BaseModel):
    enabled: dict[str, list[str]] = Field(default_factory=dict)
    request_delay_seconds: list[int] = Field(default_factory=lambda: [3, 8])
    max_pages_per_source: int = 20
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
    search_germany: list[SearchArea] = Field(default_factory=list)
    search_france: list[SearchArea] = Field(default_factory=list)
    filters_buy: Filters = Field(default_factory=Filters)
    filters_rent: Filters = Field(default_factory=lambda: Filters(max_price=2500, property_types=["apartment", "house"]))
    scrapers: ScrapersConfig = Field(default_factory=ScrapersConfig)
    reports: ReportsConfig = Field(default_factory=ReportsConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


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

    search = raw.get("search", {})
    filters = raw.get("filters", {})

    return AppConfig(
        search_germany=[SearchArea(**a) for a in search.get("germany", [])],
        search_france=[SearchArea(**a) for a in search.get("france", [])],
        filters_buy=Filters(**filters["buy"]) if "buy" in filters else Filters(),
        filters_rent=Filters(**filters["rent"]) if "rent" in filters else Filters(max_price=2500),
        scrapers=ScrapersConfig(**raw["scrapers"]) if "scrapers" in raw else ScrapersConfig(),
        reports=ReportsConfig(**raw["reports"]) if "reports" in raw else ReportsConfig(),
        proxy=ProxyConfig(**raw["proxy"]) if "proxy" in raw else ProxyConfig(),
        dashboard=DashboardConfig(**raw["dashboard"]) if "dashboard" in raw else DashboardConfig(),
        database=DatabaseConfig(**raw["database"]) if "database" in raw else DatabaseConfig(),
        logging=LoggingConfig(**raw["logging"]) if "logging" in raw else LoggingConfig(),
    )
