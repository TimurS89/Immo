"""PAP.fr scraper using httpx + BeautifulSoup."""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, PropertyData

TRANSACTION_MAP = {"buy": "vente", "rent": "location"}
PROPERTY_MAP = {"apartment": "appartement", "house": "maison", "land": "terrain"}
# Alsace department geo IDs on PAP
DEPARTMENT_GEO_IDS = {"67": "g43544", "68": "g43545"}


class PAPScraper(BaseScraper):
    SOURCE_NAME = "pap"
    COUNTRY = "FR"
    BASE_URL = "https://www.pap.fr"

    async def scrape(self) -> list[PropertyData]:
        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        }

        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
            for listing_type in ["buy", "rent"]:
                filters = self.get_filters(listing_type)
                for prop_type in filters.property_types:
                    if prop_type not in PROPERTY_MAP:
                        continue
                    for area in self.get_search_areas():
                        for dept in area.departments:
                            geo_id = DEPARTMENT_GEO_IDS.get(dept)
                            if not geo_id:
                                continue
                            try:
                                listings = await self._scrape_type(
                                    client, listing_type, prop_type, filters, geo_id
                                )
                                results.extend(listings)
                            except Exception:
                                self.logger.exception(f"Error scraping {listing_type}/{prop_type} dept {dept}")

        return results

    async def _scrape_type(
        self, client, listing_type, property_type, filters, geo_id
    ) -> list[PropertyData]:
        results = []
        transaction = TRANSACTION_MAP[listing_type]
        prop_slug = PROPERTY_MAP[property_type]

        for page_num in range(1, self.max_pages + 1):
            path = f"/annonce/{transaction}-{prop_slug}-{geo_id}-{page_num}"
            params = {}
            if filters.max_price:
                params["prix-max"] = str(int(filters.max_price))
            if filters.min_area_sqm:
                params["surface-min"] = str(int(filters.min_area_sqm))
            if filters.min_rooms:
                params["nb-pieces-min"] = str(int(filters.min_rooms))

            url = f"{self.BASE_URL}{path}"
            self.logger.info(f"Fetching {url}")
            resp = await client.get(url, params=params)

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

            next_link = soup.select_one('a[rel="next"], .pagination .next a, a:-soup-contains("Suivant")')
            if not next_link:
                break

        return results

    def _parse_page(
        self, soup: BeautifulSoup, listing_type: str, property_type: str
    ) -> list[PropertyData]:
        results = []

        cards = soup.select('.search-list-item, .search-results-item, article.announcement')
        if not cards:
            cards = soup.select('[typeof="Product"], .item-listing, .annonce')

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
        link = card.select_one('a[href*="annonce"]') or card.select_one('a[href]')
        if not link:
            return None

        href = link.get("href", "")
        ext_id_match = re.search(r"(\d+)$", href.rstrip("/"))
        if not ext_id_match:
            ext_id = re.sub(r"[^a-zA-Z0-9]", "_", href)[-50:]
        else:
            ext_id = ext_id_match.group(1)

        title_el = card.select_one('h2, .item-title, .annonce-title, .search-list-item-title')
        title = title_el.get_text(strip=True) if title_el else "Unknown"

        price = None
        price_el = card.select_one('.item-price, .price, .annonce-price')
        if price_el:
            price = self._parse_price(price_el.get_text())

        # Extract details
        text = card.get_text()
        area = None
        rooms = None

        area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", text)
        if area_match:
            area = float(area_match.group(1).replace(",", "."))

        rooms_match = re.search(r"(\d+)\s*(?:pièce|chambre|p\.)", text, re.IGNORECASE)
        if rooms_match:
            rooms = float(rooms_match.group(1))

        # City and postal code
        location_el = card.select_one('.item-description .margin-bottom-8, .item-tags, .annonce-location')
        location_text = location_el.get_text() if location_el else text
        city = ""
        postal = ""
        postal_match = re.search(r"(\d{5})\s+(\w[\w\s-]+)", location_text)
        if postal_match:
            postal = postal_match.group(1)
            city = postal_match.group(2).strip()

        listing_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"

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
