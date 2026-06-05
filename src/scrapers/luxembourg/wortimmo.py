"""wortimmo.lu scraper (FR; lower priority, some exclusives).

Parsing is fixture-tested; live ``scrape()`` runs on the workstation. Selectors
are synthetic-representative pending validation against live markup.
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup
from pydantic import ValidationError

from src.lux_monitor.schemas import ListingCreate
from src.scrapers.luxembourg.base import BROWSER_HEADERS, LuxBaseScraper
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


class WortimmoScraper(LuxBaseScraper):
    SOURCE_NAME = "wortimmo"
    BASE_URL = "https://www.wortimmo.lu"

    def search_url(self, commune: str, listing_type: str, page: int = 1) -> str:
        return f"{self.BASE_URL}/find/{commune}/?transaction={listing_type}&page={page}"

    @classmethod
    def parse_serp(cls, html: str, listing_type: str) -> list[ListingCreate]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[ListingCreate] = []
        for card in soup.select("div.annonce"):
            try:
                listing = cls._parse_card(card, listing_type)
            except ValidationError as exc:
                logger.debug("wortimmo: skipping invalid card: %s", exc)
                continue
            except Exception:  # pragma: no cover - defensive
                logger.exception("wortimmo: failed to parse a card")
                continue
            if listing is not None:
                out.append(listing)
        return out

    @classmethod
    def _parse_card(cls, card, listing_type: str) -> ListingCreate | None:
        ext_id = card.get("data-ref")
        link_el = card.select_one("a.annonce__lien")
        if not ext_id or not link_el:
            return None
        href = link_el.get("href", "")
        url = href if href.startswith("http") else f"{cls.BASE_URL}{href}"
        title = link_el.get_text(strip=True)

        price_el = card.select_one(".annonce__prix")
        charges_el = card.select_one(".annonce__charges")
        charges = parse_decimal(charges_el.get_text()) if charges_el else None
        price_val = parse_decimal(price_el.get_text()) if price_el else None

        chambres = parse_int(cls._crit(card, "chambres"))
        pieces = parse_int(cls._crit(card, "pieces"))
        surface = parse_surface_m2(cls._crit(card, "surface"))
        floor = parse_floor(cls._crit(card, "etage"))

        loc_el = card.select_one(".annonce__localite")
        loc_text = loc_el.get_text(strip=True) if loc_el else ""
        commune = loc_text.split("L-")[0].strip().rstrip(",").strip() or None
        postcode = parse_postcode(loc_text)

        energy_el = card.select_one(".annonce__energie")
        energy_class = energy_el.get("data-classe") if energy_el else None
        thermal_class = energy_el.get("data-thermique") if energy_el else None

        desc_el = card.select_one(".annonce__description")
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
            bedrooms=bedrooms_from_chambres_pieces(chambres, pieces),
            rooms_total=pieces,
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
    def _crit(card, name: str) -> str | None:
        el = card.select_one(f".annonce__criteres .critere-{name}")
        return el.get_text(strip=True) if el else None

    async def scrape(self) -> list[ListingCreate]:
        import httpx

        results: list[ListingCreate] = []
        async with httpx.AsyncClient(headers=BROWSER_HEADERS, follow_redirects=True, timeout=30) as client:
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
