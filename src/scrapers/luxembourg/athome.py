"""atHome.lu scraper (primary LU portal).

atHome renders its search results into a ``window.__INITIAL_STATE__`` JSON blob
(a Vue/SSR app), not server-rendered HTML cards — so we parse that JSON rather
than CSS selectors (far more stable). The blob holds two parallel arrays joined
by ``id``: ``search.list`` (rich property detail) and ``search.listings``
(address + ``permalink`` URL). Non-residential types (office/retail/garage) and
price-on-demand entries are filtered out. Parsing is fixture-tested; the live
``scrape()`` (httpx) runs on the workstation.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from config.luxembourg import TARGET_COMMUNES
from src.lux_monitor.schemas import ListingCreate
from src.scrapers.luxembourg.base import USER_AGENT, LuxBaseScraper

logger = logging.getLogger(__name__)

# immotype.portal_group values that are not homes — skip them.
NONRESIDENTIAL_GROUPS = {
    "office", "commercial", "retail", "land", "parking", "garage",
    "business", "industrial", "investment",
}

# JS literals that appear in the SSR state but aren't valid JSON (value position).
_JS_LITERALS = re.compile(r"([:,\[]\s*)(?:undefined|NaN|-?Infinity)\b")


def _to_float(value) -> float | None:
    if value in (None, "", "NC"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    if value in (None, "", "NC"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class AtHomeScraper(LuxBaseScraper):
    SOURCE_NAME = "athome"
    BASE_URL = "https://www.athome.lu"
    STATIC_URL = "https://i1.static.athome.eu"

    def search_url(self, commune: str, listing_type: str, page: int = 1) -> str:
        tr = "rent" if listing_type == "rent" else "buy"
        return f"{self.BASE_URL}/srp/?tr={tr}&q={commune}&page={page}"

    # --- parsing ---------------------------------------------------------------
    @staticmethod
    def _extract_initial_state(html: str) -> dict | None:
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*", html)
        if not m:
            return None
        blob = _JS_LITERALS.sub(r"\1null", html[m.end():])
        try:
            obj, _ = json.JSONDecoder().raw_decode(blob)
            return obj
        except json.JSONDecodeError:
            logger.warning("athome: could not parse __INITIAL_STATE__")
            return None

    @classmethod
    def parse_serp(cls, html: str, listing_type: str) -> list[ListingCreate]:
        state = cls._extract_initial_state(html)
        if not state:
            return []
        search = state.get("search") or {}
        details = search.get("list") or []
        meta_by_id = {m.get("id"): m for m in (search.get("listings") or [])}

        out: list[ListingCreate] = []
        for entry in details:
            try:
                listing = cls._build_listing(entry, meta_by_id.get(entry.get("id")), listing_type)
            except ValidationError as exc:
                logger.debug("athome: skipping invalid entry %s: %s", entry.get("id"), exc)
                continue
            except Exception:  # pragma: no cover - defensive
                logger.exception("athome: failed to parse entry %s", entry.get("id"))
                continue
            if listing is not None:
                out.append(listing)
        return out

    @classmethod
    def _build_listing(cls, entry: dict, meta: dict | None, listing_type: str) -> ListingCreate | None:
        # Residential only.
        group = ((entry.get("immotype") or {}).get("portal_group") or "").lower()
        if group in NONRESIDENTIAL_GROUPS:
            return None

        bedrooms = _to_int(entry.get("bedroomsCount"))
        surface = _to_float(entry.get("propertySurface"))
        if not bedrooms or not surface:  # a family home needs both
            return None

        meta = meta or {}
        price = _to_float(entry.get("price")) or _to_float(entry.get("price_min"))
        if meta.get("isPriceOnDemand") or not price:
            return None

        address = meta.get("address") or {}
        geo = entry.get("geo") or {}
        commune = (address.get("district") or "").strip() or cls._commune_from_geo(geo)
        if not commune:
            return None

        permalink = meta.get("permalink") or {}
        path = permalink.get("en") or permalink.get("fr") or permalink.get("de")
        if not path:
            return None
        url = path if path.startswith("http") else f"{cls.BASE_URL}{path}"

        txn = (entry.get("transactionType") or listing_type or "").lower()
        lt = "rent" if txn == "rent" else "buy"

        descs = entry.get("descriptions") or {}
        description = descs.get("fr") or descs.get("en") or descs.get("de") or entry.get("description") or ""
        lang = "fr" if descs.get("fr") else ("en" if descs.get("en") else "de")
        subtype = entry.get("propertySubType") or "Bien"
        if not description:
            description = f"{subtype} a {commune}."
            lang = "fr"

        garages = _to_int(entry.get("garagesCount")) or 0
        carparks = _to_int(entry.get("carparksCount")) or 0
        terraces = (_to_int(entry.get("terraceesCount")) or 0) + (_to_int(entry.get("balconiesCount")) or 0)
        street = address.get("street")

        fields = dict(
            portal=cls.SOURCE_NAME,
            portal_listing_id=str(entry.get("id")),
            url=url,
            commune=commune,
            address_text=street if street not in (None, "", "NC") else None,
            postcode=str(geo.get("postalCode") or address.get("zip") or "") or None,
            lat=_to_float(geo.get("lat")),
            lng=_to_float(geo.get("lon")),
            listing_type=lt,
            bedrooms=bedrooms,
            rooms_total=_to_int(entry.get("roomsCount")) or None,
            surface_m2=surface,
            floor=_to_int(entry.get("floorNumber")),
            has_elevator=bool(entry.get("hasElevator")),
            has_garage=garages > 0,
            parking_spaces=garages + carparks,
            has_garden=bool(entry.get("hasPrivateGarden")),
            garden_m2=_to_float(entry.get("privateGardenSurface")) or None,
            has_balcony_terrace=terraces > 0,
            construction_year=_to_int(entry.get("buildingYear")),
            description_raw=description,
            description_lang=lang,
            title=f"{subtype} - {address.get('city') or commune}",
            photos_urls=cls._photo_urls(entry.get("media")),
        )
        if lt == "rent":
            fields["rent_eur"] = price
        else:
            fields["price_eur"] = price
        return ListingCreate(**fields)

    @staticmethod
    def _commune_from_geo(geo: dict) -> str | None:
        """Map athome's cityName (e.g. 'Luxembourg-Belair') to a target commune."""
        city = (geo.get("cityName") or "").strip()
        for name in TARGET_COMMUNES:
            if city == name or city.startswith(f"{name}-") or city.startswith(f"{name} "):
                return name
        return city or None

    @classmethod
    def _photo_urls(cls, media) -> list[str]:
        items = media.get("items") if isinstance(media, dict) else media
        urls = []
        for it in items or []:
            uri = it.get("uri") if isinstance(it, dict) else None
            if uri:
                urls.append(f"{cls.STATIC_URL}{uri}")
        return urls[:10]

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
