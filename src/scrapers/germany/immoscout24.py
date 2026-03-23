"""ImmobilienScout24 scraper using Playwright with stealth."""

from __future__ import annotations

import json
import re

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from src.scrapers.base import BaseScraper, PropertyData
from src.scrapers.browser import BrowserManager


class ImmoScout24Scraper(BaseScraper):
    SOURCE_NAME = "immoscout24"
    COUNTRY = "DE"
    BASE_URL = "https://www.immobilienscout24.de"

    # URL patterns for search
    SEARCH_URLS = {
        "buy": "/Suche/de/baden-wuerttemberg/baden-baden/wohnung-kaufen?price=-{max_price}&livingspace={min_area}-&numberofrooms={min_rooms}-&pagenumber={page}",
        "buy_house": "/Suche/de/baden-wuerttemberg/baden-baden/haus-kaufen?price=-{max_price}&livingspace={min_area}-&numberofrooms={min_rooms}-&pagenumber={page}",
        "buy_land": "/Suche/de/baden-wuerttemberg/baden-baden/grundstueck-kaufen?price=-{max_price}&pagenumber={page}",
        "rent": "/Suche/de/baden-wuerttemberg/baden-baden/wohnung-mieten?price=-{max_price}&livingspace={min_area}-&numberofrooms={min_rooms}-&pagenumber={page}",
        "rent_house": "/Suche/de/baden-wuerttemberg/baden-baden/haus-mieten?price=-{max_price}&livingspace={min_area}-&numberofrooms={min_rooms}-&pagenumber={page}",
    }

    async def scrape(self) -> list[PropertyData]:
        results = []
        async with BrowserManager(self.config) as browser:
            for listing_type in ["buy", "rent"]:
                filters = self.get_filters(listing_type)
                for prop_type in filters.property_types:
                    url_key = listing_type
                    if prop_type == "house":
                        url_key = f"{listing_type}_house"
                    elif prop_type == "land":
                        url_key = f"{listing_type}_land"

                    if url_key not in self.SEARCH_URLS:
                        continue

                    try:
                        listings = await self._scrape_search(
                            browser, url_key, listing_type, prop_type, filters
                        )
                        results.extend(listings)
                    except Exception:
                        self.logger.exception(
                            f"Error scraping {self.SOURCE_NAME} {listing_type}/{prop_type}"
                        )
        return results

    async def _scrape_search(
        self,
        browser: BrowserManager,
        url_key: str,
        listing_type: str,
        property_type: str,
        filters,
    ) -> list[PropertyData]:
        results = []
        context, page = await browser.new_stealth_page("DE")

        try:
            for page_num in range(1, self.max_pages + 1):
                url = self.BASE_URL + self.SEARCH_URLS[url_key].format(
                    max_price=int(filters.max_price),
                    min_area=int(filters.min_area_sqm),
                    min_rooms=int(filters.min_rooms),
                    page=page_num,
                )

                self.logger.info(f"Fetching page {page_num}: {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self.random_delay()

                # Handle consent dialog
                await self._dismiss_consent(page)

                # Try to extract from __NEXT_DATA__ or IS24 resultlist JSON
                listings = await self._extract_listings(page, listing_type, property_type)

                if not listings:
                    self.logger.info(f"No more listings on page {page_num}, stopping")
                    break

                results.extend(listings)
                self.logger.info(f"Page {page_num}: found {len(listings)} listings")

                # Check if there's a next page
                has_next = await page.query_selector('a[data-nav-next-page="true"], [aria-label="Next page"]')
                if not has_next:
                    break

                await self.random_delay()
        finally:
            await context.close()

        return results

    async def _dismiss_consent(self, page: Page) -> None:
        """Dismiss cookie/consent dialogs."""
        try:
            consent_btn = page.locator('#usercentrics-root').get_by_role(
                'button', name=re.compile(r'Akzeptieren|Accept|Zustimmen', re.IGNORECASE)
            )
            if await consent_btn.count() > 0:
                await consent_btn.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        # Shadow DOM approach for UC
        try:
            await page.evaluate("""
                () => {
                    const shadow = document.querySelector('#usercentrics-root')?.shadowRoot;
                    if (shadow) {
                        const btn = shadow.querySelector('button[data-testid="uc-accept-all-button"]');
                        if (btn) btn.click();
                    }
                }
            """)
        except Exception:
            pass

    async def _extract_listings(
        self, page: Page, listing_type: str, property_type: str
    ) -> list[PropertyData]:
        """Extract listing data from the search results page."""
        results = []

        # Strategy 1: Try to get data from script tags (IS24 embeds JSON)
        try:
            script_data = await page.evaluate("""
                () => {
                    const scripts = document.querySelectorAll('script[type="application/json"]');
                    for (const s of scripts) {
                        try {
                            const d = JSON.parse(s.textContent);
                            if (d?.searchResponseModel?.resultlist?.resultlistEntries) return d;
                            if (d?.props?.pageProps?.searchResult) return d.props.pageProps;
                        } catch {}
                    }
                    // Try __NEXT_DATA__
                    const nd = document.querySelector('#__NEXT_DATA__');
                    if (nd) {
                        try { return JSON.parse(nd.textContent)?.props?.pageProps; } catch {}
                    }
                    return null;
                }
            """)

            if script_data:
                results = self._parse_json_results(script_data, listing_type, property_type)
                if results:
                    return results
        except Exception:
            self.logger.debug("JSON extraction failed, falling back to DOM parsing")

        # Strategy 2: DOM parsing
        try:
            cards = await page.query_selector_all('li.result-list__listing, article[data-item="result"]')
            for card in cards:
                try:
                    prop = await self._parse_card(card, page, listing_type, property_type)
                    if prop:
                        results.append(prop)
                except Exception:
                    self.logger.debug("Failed to parse listing card", exc_info=True)
        except Exception:
            self.logger.debug("DOM extraction failed", exc_info=True)

        return results

    def _parse_json_results(
        self, data: dict, listing_type: str, property_type: str
    ) -> list[PropertyData]:
        """Parse results from IS24's JSON data."""
        results = []

        # Navigate to the results array
        entries = None
        if "searchResponseModel" in data:
            entries_list = data["searchResponseModel"]["resultlist"].get("resultlistEntries", [])
            for entry_group in entries_list:
                entries = entry_group.get("resultlistEntry", [])
        elif "searchResult" in data:
            entries = data["searchResult"].get("listings", [])

        if not entries:
            return results

        for entry in entries:
            try:
                attrs = entry.get("resultlist.realEstate", entry.get("listing", {}))
                if not attrs:
                    continue

                ext_id = str(entry.get("@id", attrs.get("id", "")))
                if not ext_id:
                    continue

                title = attrs.get("title", "")
                price_val = None
                price_obj = attrs.get("price", {})
                if isinstance(price_obj, dict):
                    price_val = price_obj.get("value")
                elif isinstance(price_obj, (int, float)):
                    price_val = price_obj

                area = attrs.get("livingSpace", attrs.get("plotArea"))
                rooms = attrs.get("numberOfRooms")

                address = attrs.get("address", {})
                city = address.get("city", "Baden-Baden")
                postal = address.get("postcode", "")
                street = address.get("street", "")
                lat = address.get("wgs84Coordinate", {}).get("latitude") if isinstance(address.get("wgs84Coordinate"), dict) else None
                lon = address.get("wgs84Coordinate", {}).get("longitude") if isinstance(address.get("wgs84Coordinate"), dict) else None

                results.append(PropertyData(
                    external_id=ext_id,
                    source=self.SOURCE_NAME,
                    country="DE",
                    listing_type=listing_type,
                    property_type=property_type,
                    title=title,
                    price=float(price_val) if price_val else None,
                    rooms=float(rooms) if rooms else None,
                    living_area_sqm=float(area) if area else None,
                    address_city=city,
                    address_postal_code=str(postal),
                    address_street=street,
                    latitude=lat,
                    longitude=lon,
                    listing_url=f"{self.BASE_URL}/expose/{ext_id}",
                    raw_data=entry,
                ))
            except Exception:
                self.logger.debug(f"Failed to parse JSON entry", exc_info=True)

        return results

    async def _parse_card(
        self, card, page: Page, listing_type: str, property_type: str
    ) -> PropertyData | None:
        """Parse a single listing card from the DOM."""
        link_el = await card.query_selector('a[href*="/expose/"]')
        if not link_el:
            return None

        href = await link_el.get_attribute("href") or ""
        ext_id_match = re.search(r"/expose/(\d+)", href)
        if not ext_id_match:
            return None
        ext_id = ext_id_match.group(1)

        title_el = await card.query_selector('h2, .result-list-entry__brand-title')
        title = await title_el.inner_text() if title_el else "Unknown"

        # Extract price
        price = None
        price_el = await card.query_selector('[data-is24-qa="tilePrice"], .result-list-entry__criteria dd')
        if price_el:
            price_text = await price_el.inner_text()
            price = self._parse_price(price_text)

        # Extract area and rooms from criteria
        area = None
        rooms = None
        criteria = await card.query_selector_all('.result-list-entry__criteria dd, .result-list-entry__primary-criterion dd')
        for crit in criteria:
            text = await crit.inner_text()
            if "m²" in text:
                area = self._parse_number(text)
            elif "Zi." in text or "Zimmer" in text:
                rooms = self._parse_number(text)

        # Extract city from address
        address_el = await card.query_selector('.result-list-entry__address, [data-is24-qa="tileAddress"]')
        address_text = await address_el.inner_text() if address_el else ""
        city = "Baden-Baden"
        postal = ""
        if address_text:
            postal_match = re.search(r"(\d{5})", address_text)
            if postal_match:
                postal = postal_match.group(1)

        return PropertyData(
            external_id=ext_id,
            source=self.SOURCE_NAME,
            country="DE",
            listing_type=listing_type,
            property_type=property_type,
            title=title.strip(),
            price=price,
            rooms=rooms,
            living_area_sqm=area,
            address_city=city,
            address_postal_code=postal,
            listing_url=f"{self.BASE_URL}/expose/{ext_id}",
        )

    @staticmethod
    def _parse_price(text: str) -> float | None:
        text = text.replace(".", "").replace(",", ".").replace("€", "").strip()
        match = re.search(r"[\d.]+", text)
        return float(match.group()) if match else None

    @staticmethod
    def _parse_number(text: str) -> float | None:
        text = text.replace(",", ".")
        match = re.search(r"[\d.]+", text)
        return float(match.group()) if match else None
