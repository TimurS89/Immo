"""SeLoger scraper using Playwright with stealth (DataDome protected)."""

from __future__ import annotations

import json
import re

from playwright.async_api import Page

from src.scrapers.base import BaseScraper, PropertyData
from src.scrapers.browser import BrowserManager

# SeLoger property type codes
PROPERTY_TYPES = {
    "apartment": "1",
    "house": "2",
    "land": "4",
}

# SeLoger transaction types
TRANSACTION_TYPES = {
    "buy": "2",
    "rent": "1",
}

# Alsace department -> SeLoger location IDs (approximate, may need adjustment)
DEPARTMENT_IDS = {
    "67": "67",  # Bas-Rhin
    "68": "68",  # Haut-Rhin
}


class SeLogerScraper(BaseScraper):
    SOURCE_NAME = "seloger"
    COUNTRY = "FR"
    BASE_URL = "https://www.seloger.com"

    async def scrape(self) -> list[PropertyData]:
        results = []
        async with BrowserManager(self.config) as browser:
            for listing_type in ["buy", "rent"]:
                filters = self.get_filters(listing_type)
                for prop_type in filters.property_types:
                    for area in self.get_search_areas():
                        for dept in area.departments:
                            try:
                                listings = await self._scrape_search(
                                    browser, listing_type, prop_type, filters, dept
                                )
                                results.extend(listings)
                            except Exception:
                                self.logger.exception(
                                    f"Error scraping {listing_type}/{prop_type} dept {dept}"
                                )
        return results

    async def _scrape_search(
        self, browser, listing_type, property_type, filters, department
    ) -> list[PropertyData]:
        results = []
        context, page = await browser.new_stealth_page("FR")

        try:
            transaction = TRANSACTION_TYPES.get(listing_type, "2")
            prop_code = PROPERTY_TYPES.get(property_type, "1")

            for page_num in range(1, self.max_pages + 1):
                # SeLoger search URL pattern
                url = (
                    f"{self.BASE_URL}/list.htm"
                    f"?tri=initial"
                    f"&idtt={transaction}"
                    f"&idtypebien={prop_code}"
                    f"&cp={department}"
                    f"&pxmax={int(filters.max_price)}"
                    f"&surfacemin={int(filters.min_area_sqm)}"
                    f"&nbpieces={int(filters.min_rooms)}"
                    f"&LISTING-LISTpg={page_num}"
                )

                self.logger.info(f"Fetching {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await self._dismiss_consent(page)
                await self.random_delay()

                if await self._is_blocked(page):
                    self.logger.warning("DataDome challenge detected, stopping")
                    break

                listings = await self._extract_listings(page, listing_type, property_type)
                if not listings:
                    break

                results.extend(listings)
                self.logger.info(f"Page {page_num}: {len(listings)} listings")

                has_next = await page.query_selector('[data-testid="pagination-next"], a.pagination__next')
                if not has_next:
                    break
        finally:
            await context.close()

        return results

    async def _is_blocked(self, page: Page) -> bool:
        try:
            content = await page.content()
            return "datadome" in content.lower() or "geo.captcha" in content.lower()
        except Exception:
            return False

    async def _dismiss_consent(self, page: Page) -> None:
        try:
            btn = page.locator('#didomi-notice-agree-button, button:has-text("Accepter & Fermer")')
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

    async def _extract_listings(
        self, page: Page, listing_type: str, property_type: str
    ) -> list[PropertyData]:
        results = []

        # Try JSON-LD or embedded data
        try:
            json_data = await page.evaluate("""
                () => {
                    // Try __NEXT_DATA__
                    const nd = document.querySelector('#__NEXT_DATA__');
                    if (nd) {
                        try { return JSON.parse(nd.textContent)?.props?.pageProps; } catch {}
                    }
                    // Try window.__INITIAL_STATE__
                    if (window.__INITIAL_STATE__) return window.__INITIAL_STATE__;
                    return null;
                }
            """)
            if json_data:
                cards = (
                    json_data.get("cards", [])
                    or json_data.get("searchResults", {}).get("cards", [])
                    or json_data.get("listings", [])
                )
                for card in cards:
                    prop = self._parse_json_card(card, listing_type, property_type)
                    if prop:
                        results.append(prop)
                if results:
                    return results
        except Exception:
            self.logger.debug("JSON extraction failed")

        # Fallback: DOM parsing
        cards = await page.query_selector_all(
            '[data-testid="sl.explore.card-container"], .ListContent a[href*="annonces"], article'
        )
        for card in cards:
            try:
                prop = await self._parse_dom_card(card, listing_type, property_type)
                if prop:
                    results.append(prop)
            except Exception:
                continue

        return results

    def _parse_json_card(
        self, card: dict, listing_type: str, property_type: str
    ) -> PropertyData | None:
        ext_id = str(card.get("id", card.get("classifiedId", "")))
        if not ext_id:
            return None

        title = card.get("title", card.get("description", ""))
        price = card.get("pricing", {}).get("price", card.get("price"))
        if isinstance(price, dict):
            price = price.get("value", price.get("main"))

        rooms = card.get("rooms", card.get("nbRooms"))
        area = card.get("surface", card.get("livingArea"))
        if isinstance(area, dict):
            area = area.get("value")

        city = card.get("city", "")
        postal = str(card.get("zipCode", card.get("postalCode", "")))

        coords = card.get("coordinates", {})
        lat = coords.get("latitude", coords.get("lat"))
        lon = coords.get("longitude", coords.get("lng"))

        energy = card.get("energyPerformanceDiagnostic", {})
        if isinstance(energy, dict):
            energy = energy.get("value", energy.get("letter", ""))
        elif not isinstance(energy, str):
            energy = ""

        return PropertyData(
            external_id=ext_id,
            source=self.SOURCE_NAME,
            country="FR",
            listing_type=listing_type,
            property_type=property_type,
            title=title,
            price=float(price) if price else None,
            rooms=float(rooms) if rooms else None,
            living_area_sqm=float(area) if area else None,
            address_city=city,
            address_postal_code=postal,
            latitude=float(lat) if lat else None,
            longitude=float(lon) if lon else None,
            energy_rating=str(energy) if energy else None,
            listing_url=f"{self.BASE_URL}/annonces/{ext_id}.htm",
            raw_data=card,
        )

    async def _parse_dom_card(
        self, card, listing_type: str, property_type: str
    ) -> PropertyData | None:
        link_el = await card.query_selector('a[href*="annonces"]')
        el = link_el or card
        href = await el.get_attribute("href") or ""

        ext_id_match = re.search(r"/(\d+)\.htm", href)
        if not ext_id_match:
            return None
        ext_id = ext_id_match.group(1)

        title_el = await card.query_selector('[data-testid="sl.explore.card-title"], .card__title, h2')
        title = (await title_el.inner_text()).strip() if title_el else "Unknown"

        price = None
        price_el = await card.query_selector('[data-testid="sl.explore.card-price"], .card__price')
        if price_el:
            price = self._parse_price(await price_el.inner_text())

        listing_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"

        # Try to extract area and rooms from title/details
        area = None
        rooms = None
        details_el = await card.query_selector('[data-testid="sl.explore.card-criteria"], .card__criteria, .criteria')
        details_text = (await details_el.inner_text()) if details_el else title
        if details_text:
            area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", details_text)
            if area_match:
                area = float(area_match.group(1).replace(",", "."))
            rooms_match = re.search(r"(\d+)\s*(?:pièce|p\.)", details_text, re.IGNORECASE)
            if rooms_match:
                rooms = float(rooms_match.group(1))

        location_el = await card.query_selector('[data-testid="sl.explore.card-location"], .card__location')
        location_text = (await location_el.inner_text()).strip() if location_el else ""
        city = ""
        postal = ""
        if location_text:
            postal_match = re.search(r"(\d{5})\s+(.+)", location_text)
            if postal_match:
                postal = postal_match.group(1)
                city = postal_match.group(2).strip()
            else:
                city = location_text

        return PropertyData(
            external_id=ext_id,
            source=self.SOURCE_NAME,
            country="FR",
            listing_type=listing_type,
            property_type=property_type,
            title=title,
            price=price,
            rooms=rooms,
            living_area_sqm=area,
            address_city=city,
            address_postal_code=postal,
            listing_url=listing_url,
        )

    @staticmethod
    def _parse_price(text: str) -> float | None:
        text = text.replace(" ", "").replace("\xa0", "").replace("€", "").replace(",", ".").strip()
        m = re.search(r"[\d.]+", text)
        return float(m.group()) if m else None
