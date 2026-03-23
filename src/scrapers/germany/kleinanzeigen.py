"""Kleinanzeigen (formerly eBay Kleinanzeigen) scraper using Playwright."""

from __future__ import annotations

import re

from playwright.async_api import Page

from src.scrapers.base import BaseScraper, PropertyData
from src.scrapers.browser import BrowserManager

CATEGORY_IDS = {
    ("buy", "apartment"): "s-wohnung-kaufen",
    ("buy", "house"): "s-haus-kaufen",
    ("buy", "land"): "s-grundstuecke-garten",
    ("rent", "apartment"): "s-wohnung-mieten",
    ("rent", "house"): "s-haus-mieten",
}


class KleinanzeigenScraper(BaseScraper):
    SOURCE_NAME = "kleinanzeigen"
    COUNTRY = "DE"
    BASE_URL = "https://www.kleinanzeigen.de"

    async def scrape(self) -> list[PropertyData]:
        results = []
        async with BrowserManager(self.config) as browser:
            for listing_type in ["buy", "rent"]:
                filters = self.get_filters(listing_type)
                for prop_type in filters.property_types:
                    key = (listing_type, prop_type)
                    if key not in CATEGORY_IDS:
                        continue
                    try:
                        listings = await self._scrape_category(
                            browser, key, listing_type, prop_type, filters
                        )
                        results.extend(listings)
                    except Exception:
                        self.logger.exception(f"Error scraping {listing_type}/{prop_type}")
        return results

    async def _scrape_category(
        self, browser, key, listing_type, property_type, filters
    ) -> list[PropertyData]:
        results = []
        context, page = await browser.new_stealth_page("DE")

        try:
            category = CATEGORY_IDS[key]
            # Kleinanzeigen search: city "Baden-Baden" has location id; use text search
            base_search = f"{self.BASE_URL}/{category}/baden-baden/c203l9377"
            # Params: price range, rooms, area via query string
            params = []
            if filters.max_price:
                params.append(f"maxPrice={int(filters.max_price)}")
            if filters.min_area_sqm:
                params.append(f"minSize={int(filters.min_area_sqm)}")
            if filters.min_rooms:
                params.append(f"minRooms={int(filters.min_rooms)}")

            for page_num in range(1, self.max_pages + 1):
                url = base_search
                if page_num > 1:
                    url += f"/seite:{page_num}"
                if params:
                    url += "?" + "&".join(params)

                self.logger.info(f"Fetching {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self._dismiss_consent(page)
                await self.random_delay()

                listings = await self._extract_listings(page, listing_type, property_type)
                if not listings:
                    break

                results.extend(listings)
                self.logger.info(f"Page {page_num}: {len(listings)} listings")

                has_next = await page.query_selector('a.pagination-page[aria-label*="nächste"], a[title="Nächste Seite"]')
                if not has_next:
                    break
        finally:
            await context.close()

        return results

    async def _dismiss_consent(self, page: Page) -> None:
        try:
            btn = page.locator('#gdpr-banner-accept, button[data-testid="gdpr-banner-accept"]')
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

    async def _extract_listings(
        self, page: Page, listing_type: str, property_type: str
    ) -> list[PropertyData]:
        results = []

        cards = await page.query_selector_all(
            'article.aditem, li.ad-listitem article, [data-testid="ad-listitem"]'
        )

        for card in cards:
            try:
                prop = await self._parse_card(card, listing_type, property_type)
                if prop:
                    results.append(prop)
            except Exception:
                continue

        return results

    async def _parse_card(
        self, card, listing_type: str, property_type: str
    ) -> PropertyData | None:
        link_el = await card.query_selector('a[href*="/s-anzeige/"]')
        if not link_el:
            return None

        href = await link_el.get_attribute("href") or ""
        ext_id_match = re.search(r"/(\d+)$", href)
        if not ext_id_match:
            return None
        ext_id = ext_id_match.group(1)

        title_el = await card.query_selector('a.ellipsis, h2, [data-testid="ad-title"]')
        title = (await title_el.inner_text()).strip() if title_el else "Unknown"

        price = None
        price_el = await card.query_selector('.aditem-main--middle--price-shipping--price, [data-testid="ad-price"]')
        if price_el:
            price = self._parse_price(await price_el.inner_text())

        # Extract details (rooms, area) from text
        details_el = await card.query_selector('.aditem-main--middle--description, .aditem-main--middle--keyfacts')
        details_text = (await details_el.inner_text()) if details_el else ""

        area = None
        rooms = None
        if details_text:
            area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", details_text)
            if area_match:
                area = float(area_match.group(1).replace(",", "."))
            rooms_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:Zimmer|Zi\.)", details_text)
            if rooms_match:
                rooms = float(rooms_match.group(1).replace(",", "."))

        location_el = await card.query_selector('.aditem-main--top--left, [data-testid="ad-location"]')
        location_text = (await location_el.inner_text()).strip() if location_el else ""
        postal = ""
        if location_text:
            postal_match = re.search(r"(\d{5})", location_text)
            if postal_match:
                postal = postal_match.group(1)

        listing_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"

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
            address_city="Baden-Baden",
            address_postal_code=postal,
            listing_url=listing_url,
        )

    @staticmethod
    def _parse_price(text: str) -> float | None:
        text = text.replace(".", "").replace(",", ".").replace("€", "").replace("VB", "").strip()
        m = re.search(r"[\d.]+", text)
        return float(m.group()) if m else None
