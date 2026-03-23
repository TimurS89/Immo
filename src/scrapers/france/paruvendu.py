"""ParuVendu scraper using httpx + BeautifulSoup."""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, PropertyData

SEARCH_PATHS = {
    ("buy", "apartment"): "/immobilier/annonceimmo/V/R{dept}/A/T2",
    ("buy", "house"): "/immobilier/annonceimmo/V/R{dept}/A/T1",
    ("buy", "land"): "/immobilier/annonceimmo/V/R{dept}/A/T4",
    ("rent", "apartment"): "/immobilier/annonceimmo/L/R{dept}/A/T2",
    ("rent", "house"): "/immobilier/annonceimmo/L/R{dept}/A/T1",
}


class ParuVenduScraper(BaseScraper):
    SOURCE_NAME = "paruvendu"
    COUNTRY = "FR"
    BASE_URL = "https://www.paruvendu.fr"

    async def scrape(self) -> list[PropertyData]:
        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "fr-FR,fr;q=0.9",
        }

        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
            for listing_type in ["buy", "rent"]:
                filters = self.get_filters(listing_type)
                for prop_type in filters.property_types:
                    key = (listing_type, prop_type)
                    if key not in SEARCH_PATHS:
                        continue
                    for area in self.get_search_areas():
                        for dept in area.departments:
                            try:
                                listings = await self._scrape_type(
                                    client, key, listing_type, prop_type, filters, dept
                                )
                                results.extend(listings)
                            except Exception:
                                self.logger.exception(
                                    f"Error scraping {listing_type}/{prop_type} dept {dept}"
                                )

        return results

    async def _scrape_type(
        self, client, key, listing_type, property_type, filters, department
    ) -> list[PropertyData]:
        results = []

        for page_num in range(1, self.max_pages + 1):
            path = SEARCH_PATHS[key].format(dept=department)
            params = {
                "p": str(page_num),
            }
            if filters.max_price:
                params["px1"] = str(int(filters.max_price))
            if filters.min_area_sqm:
                params["su0"] = str(int(filters.min_area_sqm))
            if filters.min_rooms:
                params["nb0"] = str(int(filters.min_rooms))

            url = f"{self.BASE_URL}{path}"
            self.logger.info(f"Fetching {url} page {page_num}")
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

            next_link = soup.select_one('a.page_suivante, a[rel="next"], .pagination .next')
            if not next_link:
                break

        return results

    def _parse_page(
        self, soup: BeautifulSoup, listing_type: str, property_type: str
    ) -> list[PropertyData]:
        results = []

        cards = soup.select('.ergov3-annonce, .annonce-immobiliere, .annonce, li.lazyload')
        if not cards:
            cards = soup.select('[itemtype*="Offer"], .liste_annonces > div, .search-result-item')

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
        link = card.select_one('a[href*="immobilier"]') or card.select_one('a[href]')
        if not link:
            return None

        href = link.get("href", "")
        ext_id_match = re.search(r"/(\d+)$", href.rstrip("/"))
        if not ext_id_match:
            ext_id_match = re.search(r"(\d{6,})", href)
        if not ext_id_match:
            return None
        ext_id = ext_id_match.group(1)

        title_el = card.select_one('.ergov3-title, h2, h3, .annonce-titre, [itemprop="name"]')
        title = title_el.get_text(strip=True) if title_el else "Unknown"

        price = None
        price_el = card.select_one('.ergov3-prix, .price, [itemprop="price"], .annonce-prix')
        if price_el:
            price = self._parse_price(price_el.get_text())

        text = card.get_text()
        area = None
        rooms = None

        area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", text)
        if area_match:
            area = float(area_match.group(1).replace(",", "."))

        rooms_match = re.search(r"(\d+)\s*(?:pièce|chambre|p\.)", text, re.IGNORECASE)
        if rooms_match:
            rooms = float(rooms_match.group(1))

        city = ""
        postal = ""
        location_match = re.search(r"(\d{5})\s+([\w\s-]+)", text)
        if location_match:
            postal = location_match.group(1)
            city = location_match.group(2).strip()

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
