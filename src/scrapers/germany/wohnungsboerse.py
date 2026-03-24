"""Wohnungsboerse.net scraper using httpx + BeautifulSoup."""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, PropertyData

SEARCH_URLS = {
    ("buy", "apartment"): "/immomarkt/baden-baden/eigentumswohnungen?max_preis={max_price}&min_zimmer={min_rooms}&min_flaeche={min_area}&seite={page}",
    ("buy", "house"): "/immomarkt/baden-baden/haeuser-kaufen?max_preis={max_price}&min_zimmer={min_rooms}&min_flaeche={min_area}&seite={page}",
    ("buy", "land"): "/immomarkt/baden-baden/grundstuecke?max_preis={max_price}&seite={page}",
    ("rent", "apartment"): "/immomarkt/baden-baden/mietwohnungen?max_preis={max_price}&min_zimmer={min_rooms}&min_flaeche={min_area}&seite={page}",
    ("rent", "house"): "/immomarkt/baden-baden/haeuser-mieten?max_preis={max_price}&min_zimmer={min_rooms}&min_flaeche={min_area}&seite={page}",
}


class WohnungsboerseScraper(BaseScraper):
    SOURCE_NAME = "wohnungsboerse"
    COUNTRY = "DE"
    BASE_URL = "https://www.wohnungsboerse.net"

    async def scrape(self) -> list[PropertyData]:
        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        }

        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
            for listing_type in ["buy", "rent"]:
                filters = self.get_filters(listing_type)
                for prop_type in filters.property_types:
                    key = (listing_type, prop_type)
                    if key not in SEARCH_URLS:
                        continue
                    try:
                        listings = await self._scrape_type(
                            client, key, listing_type, prop_type, filters
                        )
                        results.extend(listings)
                    except Exception:
                        self.logger.exception(f"Error scraping {listing_type}/{prop_type}")

        return results

    async def _scrape_type(
        self, client, key, listing_type, property_type, filters
    ) -> list[PropertyData]:
        results = []

        for page_num in range(1, self.max_pages + 1):
            url = self.BASE_URL + SEARCH_URLS[key].format(
                max_price=int(filters.max_price),
                min_rooms=int(filters.min_rooms),
                min_area=int(filters.min_area_sqm),
                page=page_num,
            )

            self.logger.info(f"Fetching {url}")
            resp = await client.get(url)
            if resp.status_code != 200:
                self.logger.warning(f"HTTP {resp.status_code} for {url}")
                break

            await self.random_delay()
            soup = BeautifulSoup(resp.text, "lxml")
            listings = self._parse_page(soup, listing_type, property_type)

            if not listings:
                break

            results.extend(listings)
            self.logger.info(f"Page {page_num}: {len(listings)} listings")

            # Check for next page
            next_link = soup.select_one('a[rel="next"], a.pagination-next, .next a')
            if not next_link:
                break

        return results

    def _parse_page(
        self, soup: BeautifulSoup, listing_type: str, property_type: str
    ) -> list[PropertyData]:
        results = []

        # Wohnungsboerse uses various card layouts
        cards = soup.select('.estate-item, .search-result-item, article.listing, .object-list-item')
        if not cards:
            cards = soup.select('[itemtype*="Residence"], [itemtype*="Product"]')

        for card in cards:
            try:
                prop = self._parse_card(card, listing_type, property_type)
                if prop:
                    results.append(prop)
            except Exception:
                continue

        return results

    def _parse_card(
        self, card, listing_type: str, property_type: str
    ) -> PropertyData | None:
        link = card.select_one('a[href*="/immo/"]') or card.select_one('a[href]')
        if not link:
            return None

        href = link.get("href", "")
        ext_id_match = re.search(r"/(\d+)(?:\.html)?$", href)
        if not ext_id_match:
            # Try to use the full path as ID
            ext_id = re.sub(r"[^a-zA-Z0-9]", "_", href)[-50:]
        else:
            ext_id = ext_id_match.group(1)

        title_el = card.select_one('h2, h3, .title, [itemprop="name"]')
        title = title_el.get_text(strip=True) if title_el else "Unknown"

        price = None
        price_el = card.select_one('.price, [itemprop="price"], .estate-price')
        if price_el:
            price = self._parse_price(price_el.get_text())

        area = None
        rooms = None

        # Look for details in text
        details_text = card.get_text()
        area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", details_text)
        if area_match:
            area = float(area_match.group(1).replace(",", "."))

        rooms_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:Zimmer|Zi\.)", details_text)
        if rooms_match:
            rooms = float(rooms_match.group(1).replace(",", "."))

        postal = ""
        city = ""
        postal_match = re.search(r"(\d{5})\s+([A-ZÄÖÜa-zäöüß][\w\s-]+)", details_text)
        if postal_match:
            postal = postal_match.group(1)
            city = postal_match.group(2).strip()

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
            address_city=city,
            address_postal_code=postal,
            listing_url=listing_url,
        )

    @staticmethod
    def _parse_price(text: str) -> float | None:
        text = text.replace(".", "").replace(",", ".").replace("€", "").strip()
        m = re.search(r"[\d.]+", text)
        return float(m.group()) if m else None
