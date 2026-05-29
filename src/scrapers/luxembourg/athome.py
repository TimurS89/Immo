"""atHome.lu scraper (primary LU portal).

Parsing is fixture-tested; the live ``scrape()`` (httpx for SERP, Playwright for
JS-heavy detail pages) is validated on the workstation. SERP markup selectors are
synthetic-representative and must be re-checked against live HTML before first run.
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


class AtHomeScraper(LuxBaseScraper):
    SOURCE_NAME = "athome"
    BASE_URL = "https://www.athome.lu"

    def search_url(self, commune: str, listing_type: str, page: int = 1) -> str:
        tr = "rent" if listing_type == "rent" else "buy"
        return f"{self.BASE_URL}/srp/?tr={tr}&q={commune}&page={page}"

    @classmethod
    def parse_serp(cls, html: str, listing_type: str) -> list[ListingCreate]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[ListingCreate] = []
        for card in soup.select("article.property-card"):
            try:
                listing = cls._parse_card(card, listing_type)
            except ValidationError as exc:
                logger.debug("athome: skipping invalid card: %s", exc)
                continue
            except Exception:  # pragma: no cover - defensive
                logger.exception("athome: failed to parse a card")
                continue
            if listing is not None:
                out.append(listing)
        return out

    @classmethod
    def _parse_card(cls, card, listing_type: str) -> ListingCreate | None:
        ext_id = card.get("data-id")
        link_el = card.select_one("a.property-card__link")
        if not ext_id or not link_el:
            return None
        href = link_el.get("href", "")
        url = href if href.startswith("http") else f"{cls.BASE_URL}{href}"

        title_el = card.select_one(".property-card__title")
        title = title_el.get_text(strip=True) if title_el else "athome listing"

        # Price block: rent (or sale price) plus an optional charges span.
        price_el = card.select_one(".property-card__price")
        charges_el = card.select_one(".property-card__charges")
        charges = None
        price_val = None
        if price_el:
            if charges_el:
                charges = parse_decimal(charges_el.get_text())
                charges_el.extract()  # remove so it doesn't pollute the price text
            price_val = parse_decimal(price_el.get_text())

        chambres = parse_int(cls._char(card, "chambres"))
        pieces = parse_int(cls._char(card, "pieces"))
        surface = parse_surface_m2(cls._char(card, "surface"))
        floor = parse_floor(cls._char(card, "etage"))

        loc_el = card.select_one(".property-card__location")
        loc_text = loc_el.get_text(strip=True) if loc_el else ""
        commune = loc_text.split("(")[0].strip() or None
        postcode = parse_postcode(loc_text)

        energy_el = card.select_one(".property-card__energy")
        energy_class = energy_el.get("data-energy") if energy_el else None
        thermal_class = energy_el.get("data-thermal") if energy_el else None

        desc_el = card.select_one(".property-card__desc")
        description = desc_el.get_text(strip=True) if desc_el else title
        blob = f"{title} {description}"
        features = detect_features(blob)

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
            **features,
        )
        if listing_type == "rent":
            fields["rent_eur"] = price_val
            fields["charges_eur"] = charges
        else:
            fields["price_eur"] = price_val

        return ListingCreate(**fields)

    @staticmethod
    def _char(card, css_class: str) -> str | None:
        el = card.select_one(f".property-card__chars .{css_class}")
        return el.get_text(strip=True) if el else None

    async def scrape(self) -> list[ListingCreate]:
        """Live scrape (workstation): httpx SERP per commune + listing type."""
        import httpx

        results: list[ListingCreate] = []
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "fr-LU,fr;q=0.9"}
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
