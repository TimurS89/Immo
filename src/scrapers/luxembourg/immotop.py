"""immotop.lu scraper (bilingual EN/FR).

Parsing is fixture-tested; live ``scrape()`` runs on the workstation. Selectors
are synthetic-representative pending validation against live markup.
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup
from pydantic import ValidationError

from src.lux_monitor.schemas import ListingCreate
from src.scrapers.luxembourg.base import USER_AGENT, LuxBaseScraper
from src.scrapers.luxembourg.parsing import (
    bedrooms_from_chambres_pieces,
    detect_features,
    detect_lang,
    parse_decimal,
    parse_floor,
    parse_int,
    parse_postcode,
    parse_surface_m2,
)

logger = logging.getLogger(__name__)


class ImmotopScraper(LuxBaseScraper):
    SOURCE_NAME = "immotop"
    BASE_URL = "https://www.immotop.lu"

    def search_url(self, commune: str, listing_type: str, page: int = 1) -> str:
        kind = "rent" if listing_type == "rent" else "sale"
        slug = commune.lower().replace(" ", "-")
        return (
            f"{self.BASE_URL}/en/search-houses-apartments-{kind}/luxembourg/{slug}/"
            f"?pag={page}"
        )

    @classmethod
    def parse_serp(cls, html: str, listing_type: str) -> list[ListingCreate]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[ListingCreate] = []
        for card in soup.select("div.in-listingCard"):
            try:
                listing = cls._parse_card(card, listing_type)
            except ValidationError as exc:
                logger.debug("immotop: skipping invalid card: %s", exc)
                continue
            except Exception:  # pragma: no cover - defensive
                logger.exception("immotop: failed to parse a card")
                continue
            if listing is not None:
                out.append(listing)
        return out

    @classmethod
    def _parse_card(cls, card, listing_type: str) -> ListingCreate | None:
        ext_id = card.get("data-id")
        link_el = card.select_one("a.in-listingCard__title")
        if not ext_id or not link_el:
            return None
        href = link_el.get("href", "")
        url = href if href.startswith("http") else f"{cls.BASE_URL}{href}"
        title = link_el.get_text(strip=True)

        price_el = card.select_one(".in-listingCard__price")
        charges_el = card.select_one(".in-listingCard__charges")
        charges = parse_decimal(charges_el.get_text()) if charges_el else None
        price_val = parse_decimal(price_el.get_text()) if price_el else None

        bedrooms = parse_int(cls._feat(card, "bedrooms"))
        rooms = parse_int(cls._feat(card, "rooms"))
        surface = parse_surface_m2(cls._feat(card, "surface"))
        floor = parse_floor(cls._feat(card, "floor"))

        loc_el = card.select_one(".in-listingCard__location")
        loc_text = loc_el.get_text(strip=True) if loc_el else ""
        commune = loc_text.split(",")[0].strip() or None
        postcode = parse_postcode(loc_text)

        energy_el = card.select_one(".in-listingCard__energy")
        energy_class = energy_el.get("data-energy") if energy_el else None
        thermal_class = energy_el.get("data-thermal") if energy_el else None

        desc_el = card.select_one(".in-listingCard__description")
        description = desc_el.get_text(strip=True) if desc_el else title
        blob = f"{title} {description}"

        photos = [img.get("src") for img in card.select("img") if img.get("src")]

        fields = dict(
            portal=cls.SOURCE_NAME,
            portal_listing_id=str(ext_id),
            url=url,
            commune=commune or "Unknown",
            postcode=postcode,
            listing_type=listing_type,
            bedrooms=bedrooms_from_chambres_pieces(bedrooms, rooms),
            rooms_total=rooms,
            surface_m2=surface,
            floor=floor,
            energy_class=energy_class,
            thermal_class=thermal_class,
            description_raw=description,
            description_lang=detect_lang(blob),
            title=title,
            photos_urls=photos,
            **detect_features(blob),
        )
        if listing_type == "rent":
            fields["rent_eur"] = price_val
            fields["charges_eur"] = charges
        else:
            fields["price_eur"] = price_val
        return ListingCreate(**fields)

    @staticmethod
    def _feat(card, name: str) -> str | None:
        el = card.select_one(f".in-listingCard__features .feature--{name}")
        return el.get_text(strip=True) if el else None

    async def scrape(self) -> list[ListingCreate]:
        import httpx

        results: list[ListingCreate] = []
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "en;q=0.9,fr-LU;q=0.8"}
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
            for listing_type in ("rent", "buy"):
                for commune in self.communes():
                    for page in range(1, self.max_pages + 1):
                        url = self.search_url(commune, listing_type, page)
                        self.logger.info("GET %s", url)
                        resp = await client.get(url)
                        if resp.status_code != 200:
                            break
                        page_listings = self.parse_serp(resp.text, listing_type)
                        if not page_listings:
                            break
                        results.extend(page_listings)
                        await self.random_delay()
        return results
