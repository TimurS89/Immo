"""LeBonCoin scraper using Playwright with stealth (DataDome protected)."""

from __future__ import annotations

import json
import re

from playwright.async_api import Page

from src.scrapers.base import BaseScraper, PropertyData
from src.scrapers.browser import BrowserManager

# LeBonCoin category mappings
CATEGORIES = {
    ("buy", "apartment"): {"category": "9", "real_estate_type": "1"},
    ("buy", "house"): {"category": "9", "real_estate_type": "2"},
    ("buy", "land"): {"category": "9", "real_estate_type": "4"},
    ("rent", "apartment"): {"category": "10", "real_estate_type": "1"},
    ("rent", "house"): {"category": "10", "real_estate_type": "2"},
}

# Alsace departments
ALSACE_LOCATIONS = {
    "67": "d_67",  # Bas-Rhin
    "68": "d_68",  # Haut-Rhin
}


class LeBonCoinScraper(BaseScraper):
    SOURCE_NAME = "leboncoin"
    COUNTRY = "FR"
    BASE_URL = "https://www.leboncoin.fr"

    async def scrape(self) -> list[PropertyData]:
        results = []
        async with BrowserManager(self.config) as browser:
            for listing_type in ["buy", "rent"]:
                filters = self.get_filters(listing_type)
                for prop_type in filters.property_types:
                    key = (listing_type, prop_type)
                    if key not in CATEGORIES:
                        continue
                    for area in self.get_search_areas():
                        for dept in area.departments:
                            try:
                                listings = await self._scrape_department(
                                    browser, key, listing_type, prop_type, filters, dept
                                )
                                results.extend(listings)
                            except Exception:
                                self.logger.exception(
                                    f"Error scraping {listing_type}/{prop_type} dept {dept}"
                                )
        return results

    async def _scrape_department(
        self, browser, key, listing_type, property_type, filters, department
    ) -> list[PropertyData]:
        results = []
        context, page = await browser.new_stealth_page("FR")

        try:
            cat_info = CATEGORIES[key]
            cat_path = "ventes_immobilieres" if listing_type == "buy" else "locations"

            for page_num in range(1, self.max_pages + 1):
                url = (
                    f"{self.BASE_URL}/recherche/{cat_path}"
                    f"?departments={department}"
                    f"&real_estate_type={cat_info['real_estate_type']}"
                    f"&price=min-{int(filters.max_price)}"
                    f"&rooms={int(filters.min_rooms)}-all"
                    f"&square={int(filters.min_area_sqm)}-all"
                    f"&page={page_num}"
                )

                self.logger.info(f"Fetching {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await self._dismiss_consent(page)
                await self.random_delay()

                # Check for DataDome challenge
                if await self._is_blocked(page):
                    self.logger.warning("DataDome challenge detected, stopping this search")
                    break

                listings = await self._extract_listings(page, listing_type, property_type, department)
                if not listings:
                    break

                results.extend(listings)
                self.logger.info(f"Page {page_num} dept {department}: {len(listings)} listings")

                # Check pagination
                has_next = await page.query_selector('a[title="Page suivante"], [data-qa-id="adlist_pagination_next"]')
                if not has_next:
                    break
        finally:
            await context.close()

        return results

    async def _is_blocked(self, page: Page) -> bool:
        """Check if DataDome has blocked the request."""
        try:
            content = await page.content()
            return "datadome" in content.lower() or "captcha" in content.lower()
        except Exception:
            return False

    async def _dismiss_consent(self, page: Page) -> None:
        try:
            btn = page.locator('button#didomi-notice-agree-button, button:has-text("Accepter")')
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

    async def _extract_listings(
        self, page: Page, listing_type: str, property_type: str, department: str
    ) -> list[PropertyData]:
        results = []

        # Try to intercept API response data from Next.js
        try:
            data = await page.evaluate("""
                () => {
                    const el = document.querySelector('#__NEXT_DATA__');
                    if (el) return JSON.parse(el.textContent);
                    return null;
                }
            """)
            if data:
                page_props = data.get("props", {}).get("pageProps", {})
                ads = page_props.get("searchData", {}).get("ads", [])
                if not ads:
                    ads = page_props.get("ads", [])
                for ad in ads:
                    prop = self._parse_ad(ad, listing_type, property_type)
                    if prop:
                        results.append(prop)
                if results:
                    return results
        except Exception:
            self.logger.debug("__NEXT_DATA__ extraction failed")

        # Fallback: DOM parsing
        cards = await page.query_selector_all(
            '[data-qa-id="aditem_container"], [data-test-id="ad"], article a[href*="/ad/"]'
        )
        for card in cards:
            try:
                prop = await self._parse_card(card, listing_type, property_type)
                if prop:
                    results.append(prop)
            except Exception:
                continue

        return results

    def _parse_ad(
        self, ad: dict, listing_type: str, property_type: str
    ) -> PropertyData | None:
        ext_id = str(ad.get("list_id", ad.get("id", "")))
        if not ext_id:
            return None

        title = ad.get("subject", ad.get("title", ""))
        price = None
        price_list = ad.get("price", [])
        if isinstance(price_list, list) and price_list:
            price = float(price_list[0])
        elif isinstance(price_list, (int, float)):
            price = float(price_list)

        attrs = {}
        for attr in ad.get("attributes", []):
            attrs[attr.get("key", "")] = attr.get("value", "")

        rooms = None
        if "rooms" in attrs:
            try:
                rooms = float(attrs["rooms"])
            except (ValueError, TypeError):
                pass

        area = None
        if "square" in attrs:
            try:
                area = float(attrs["square"])
            except (ValueError, TypeError):
                pass

        location = ad.get("location", {})
        city = location.get("city", "")
        postal = location.get("zipcode", "")
        dept = location.get("department_id", "")
        lat = location.get("lat")
        lon = location.get("lng")

        energy = attrs.get("energy_rate", attrs.get("dpe", ""))

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
            address_postal_code=str(postal),
            latitude=float(lat) if lat else None,
            longitude=float(lon) if lon else None,
            energy_rating=energy,
            listing_url=f"{self.BASE_URL}/ad/immobilier/{ext_id}.htm",
            raw_data=ad,
        )

    async def _parse_card(
        self, card, listing_type: str, property_type: str
    ) -> PropertyData | None:
        link_el = await card.query_selector('a[href*="/ad/"]')
        el = link_el or card
        href = await el.get_attribute("href") or ""

        ext_id_match = re.search(r"/(\d+)\.htm", href)
        if not ext_id_match:
            return None
        ext_id = ext_id_match.group(1)

        title_el = await card.query_selector('[data-qa-id="aditem_title"], p, h2')
        title = (await title_el.inner_text()).strip() if title_el else "Unknown"

        price = None
        price_el = await card.query_selector('[data-qa-id="aditem_price"], .price')
        if price_el:
            price = self._parse_price(await price_el.inner_text())

        listing_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"

        return PropertyData(
            external_id=ext_id,
            source=self.SOURCE_NAME,
            country="FR",
            listing_type=listing_type,
            property_type=property_type,
            title=title,
            price=price,
            listing_url=listing_url,
        )

    @staticmethod
    def _parse_price(text: str) -> float | None:
        text = text.replace(" ", "").replace("\xa0", "").replace("€", "").replace(",", ".").strip()
        m = re.search(r"[\d.]+", text)
        return float(m.group()) if m else None
