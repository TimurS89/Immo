"""Immowelt scraper using Playwright."""

from __future__ import annotations

import json
import re

from playwright.async_api import Page

from src.scrapers.base import BaseScraper, PropertyData
from src.scrapers.browser import BrowserManager

# Immowelt URL patterns
SEARCH_TEMPLATES = {
    ("buy", "apartment"): "/liste/baden-baden/wohnungen/kaufen?pma={max_price}&ama={min_area}&rmi={min_rooms}&sp={page}",
    ("buy", "house"): "/liste/baden-baden/haeuser/kaufen?pma={max_price}&ama={min_area}&rmi={min_rooms}&sp={page}",
    ("buy", "land"): "/liste/baden-baden/grundstuecke/kaufen?pma={max_price}&sp={page}",
    ("rent", "apartment"): "/liste/baden-baden/wohnungen/mieten?pma={max_price}&ama={min_area}&rmi={min_rooms}&sp={page}",
    ("rent", "house"): "/liste/baden-baden/haeuser/mieten?pma={max_price}&ama={min_area}&rmi={min_rooms}&sp={page}",
}


class ImmoweltScraper(BaseScraper):
    SOURCE_NAME = "immowelt"
    COUNTRY = "DE"
    BASE_URL = "https://www.immowelt.de"

    async def scrape(self) -> list[PropertyData]:
        results = []
        async with BrowserManager(self.config) as browser:
            for listing_type in ["buy", "rent"]:
                filters = self.get_filters(listing_type)
                for prop_type in filters.property_types:
                    key = (listing_type, prop_type)
                    if key not in SEARCH_TEMPLATES:
                        continue
                    try:
                        listings = await self._scrape_type(
                            browser, key, listing_type, prop_type, filters
                        )
                        results.extend(listings)
                    except Exception:
                        self.logger.exception(f"Error scraping {listing_type}/{prop_type}")
        return results

    async def _scrape_type(
        self, browser: BrowserManager, key, listing_type, property_type, filters
    ) -> list[PropertyData]:
        results = []
        context, page = await browser.new_stealth_page("DE")

        try:
            for page_num in range(1, self.max_pages + 1):
                url = self.BASE_URL + SEARCH_TEMPLATES[key].format(
                    max_price=int(filters.max_price),
                    min_area=int(filters.min_area_sqm),
                    min_rooms=int(filters.min_rooms),
                    page=page_num,
                )
                self.logger.info(f"Fetching {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self._dismiss_consent(page)
                await self.random_delay()

                listings = await self._extract_listings(page, listing_type, property_type)
                if not listings:
                    break

                results.extend(listings)
                self.logger.info(f"Page {page_num}: {len(listings)} listings")

                has_next = await page.query_selector('a[data-testid="paging-next"], a[aria-label="Nächste Seite"]')
                if not has_next:
                    break
        finally:
            await context.close()

        return results

    async def _dismiss_consent(self, page: Page) -> None:
        try:
            btn = page.locator('button:has-text("Alle akzeptieren"), button:has-text("Accept All")')
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

    async def _extract_listings(
        self, page: Page, listing_type: str, property_type: str
    ) -> list[PropertyData]:
        results = []

        # Try __NEXT_DATA__ first (Immowelt uses Next.js)
        try:
            next_data = await page.evaluate("""
                () => {
                    const el = document.querySelector('#__NEXT_DATA__');
                    if (el) return JSON.parse(el.textContent);
                    return null;
                }
            """)
            if next_data:
                page_props = next_data.get("props", {}).get("pageProps", {})
                items = page_props.get("listItems", page_props.get("results", []))
                for item in items:
                    prop = self._parse_next_data_item(item, listing_type, property_type)
                    if prop:
                        results.append(prop)
                if results:
                    return results
        except Exception:
            self.logger.debug("__NEXT_DATA__ extraction failed")

        # Fallback: DOM parsing
        cards = await page.query_selector_all('[data-testid="listitem"], .listitem_wrap')
        for card in cards:
            try:
                prop = await self._parse_card(card, listing_type, property_type)
                if prop:
                    results.append(prop)
            except Exception:
                continue

        return results

    def _parse_next_data_item(
        self, item: dict, listing_type: str, property_type: str
    ) -> PropertyData | None:
        ext_id = str(item.get("onlId", item.get("id", "")))
        if not ext_id:
            return None

        title = item.get("title", "")
        price = item.get("price", {})
        price_val = price.get("value") if isinstance(price, dict) else price

        area = item.get("areas", {})
        living_area = area.get("livingArea", {}).get("value") if isinstance(area, dict) else None
        plot_area = area.get("plotArea", {}).get("value") if isinstance(area, dict) else None

        rooms = item.get("rooms")
        if isinstance(rooms, dict):
            rooms = rooms.get("value")

        geo = item.get("geo", {})
        city = geo.get("city", {}).get("name", "") if isinstance(geo, dict) else ""
        postal = str(geo.get("zip", "")) if isinstance(geo, dict) else ""
        lat = geo.get("lat") if isinstance(geo, dict) else None
        lon = geo.get("lng") if isinstance(geo, dict) else None

        return PropertyData(
            external_id=ext_id,
            source=self.SOURCE_NAME,
            country="DE",
            listing_type=listing_type,
            property_type=property_type,
            title=title,
            price=float(price_val) if price_val else None,
            rooms=float(rooms) if rooms else None,
            living_area_sqm=float(living_area) if living_area else None,
            plot_area_sqm=float(plot_area) if plot_area else None,
            address_city=city,
            address_postal_code=postal,
            latitude=lat,
            longitude=lon,
            listing_url=f"{self.BASE_URL}/expose/{ext_id}",
            raw_data=item,
        )

    async def _parse_card(
        self, card, listing_type: str, property_type: str
    ) -> PropertyData | None:
        link_el = await card.query_selector('a[href*="/expose/"]')
        if not link_el:
            return None

        href = await link_el.get_attribute("href") or ""
        ext_id_match = re.search(r"/expose/([a-zA-Z0-9]+)", href)
        if not ext_id_match:
            return None
        ext_id = ext_id_match.group(1)

        title_el = await card.query_selector('h2, .listitem_header')
        title = (await title_el.inner_text()).strip() if title_el else "Unknown"

        price = None
        price_el = await card.query_selector('[data-testid="price"], .hardfact.price')
        if price_el:
            price = self._parse_price(await price_el.inner_text())

        area = None
        area_el = await card.query_selector('[data-testid="area"], .hardfact.area')
        if area_el:
            area = self._parse_number(await area_el.inner_text())

        rooms = None
        rooms_el = await card.query_selector('[data-testid="rooms"], .hardfact.rooms')
        if rooms_el:
            rooms = self._parse_number(await rooms_el.inner_text())

        return PropertyData(
            external_id=ext_id,
            source=self.SOURCE_NAME,
            country="DE",
            listing_type=listing_type,
            property_type=property_type,
            title=title,
            price=price,
            rooms=rooms,
            living_area_sqm=area,
            address_city="",
            listing_url=f"{self.BASE_URL}/expose/{ext_id}",
        )

    @staticmethod
    def _parse_price(text: str) -> float | None:
        text = text.replace(".", "").replace(",", ".").replace("€", "").strip()
        m = re.search(r"[\d.]+", text)
        return float(m.group()) if m else None

    @staticmethod
    def _parse_number(text: str) -> float | None:
        text = text.replace(",", ".")
        m = re.search(r"[\d.]+", text)
        return float(m.group()) if m else None
