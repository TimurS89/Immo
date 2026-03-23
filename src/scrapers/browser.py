"""Shared Playwright browser management with stealth."""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import AsyncGenerator

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright_stealth import stealth_async

from src.config import AppConfig

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
]

LOCALES = {
    "DE": {"locale": "de-DE", "timezone": "Europe/Berlin"},
    "FR": {"locale": "fr-FR", "timezone": "Europe/Paris"},
}


class BrowserManager:
    """Manages Playwright browser instances with stealth configuration."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._playwright = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> BrowserManager:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.config.scrapers.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        logger.info("Browser launched (headless=%s)", self.config.scrapers.headless)
        return self

    async def __aexit__(self, *args) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser closed")

    async def new_context(self, country: str = "DE") -> BrowserContext:
        """Create a new browser context with stealth settings."""
        locale_info = LOCALES.get(country, LOCALES["DE"])
        viewport = random.choice(VIEWPORTS)
        user_agent = random.choice(USER_AGENTS)

        proxy_settings = None
        if self.config.proxy.enabled:
            proxy_settings = self._get_proxy()

        context = await self._browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            locale=locale_info["locale"],
            timezone_id=locale_info["timezone"],
            proxy=proxy_settings,
            java_script_enabled=True,
        )
        return context

    async def new_stealth_page(self, country: str = "DE") -> tuple[BrowserContext, Page]:
        """Create a new stealth page. Returns (context, page) - caller must close context."""
        context = await self.new_context(country)
        page = await context.new_page()
        await stealth_async(page)
        return context, page

    def _get_proxy(self) -> dict | None:
        """Load a random proxy from the proxy file."""
        proxy_file = Path(self.config.proxy.file)
        if not proxy_file.exists():
            logger.warning("Proxy file not found: %s", proxy_file)
            return None

        proxies = [
            line.strip()
            for line in proxy_file.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if not proxies:
            return None

        proxy_url = random.choice(proxies)
        return {"server": proxy_url}
