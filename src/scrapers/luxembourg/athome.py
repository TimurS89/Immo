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
from dataclasses import dataclass, field

from pydantic import ValidationError

from config.luxembourg import COMMUNE_HKEYS, TARGET_COMMUNES
from src.lux_monitor.schemas import ListingCreate
from src.scrapers.luxembourg.base import USER_AGENT, LuxBaseScraper

logger = logging.getLogger(__name__)

# How athome encodes our searches. NOTE: athome has no working URL facet for
# "furnished" (the ire/iere params are ignored — verified live: rent and furnished
# searches returned identical totals). So we run ONE rental search and classify
# furnished vs long-term from the listing text below.
TRANSACTION_PARAMS: dict[str, str] = {
    "rent": "tr=rent",
    "buy": "tr=buy",
}

# Furnished detection (FR/DE/EN). Match the *adjective* "meublé/meublée/..." (with
# its accent) — NOT the accent-stripped "meuble(s)" which means "furniture" and
# would false-positive on "beaux meubles". Exclude explicit negations.
_FURNISHED_RE = re.compile(r"(meubl(?:é|ée|és|ées))|möbliert|furnished", re.IGNORECASE)
_NOT_FURNISHED_RE = re.compile(r"non\s+meubl|nicht\s+möbliert|unfurnished", re.IGNORECASE)

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


@dataclass
class PageResult:
    """One SERP page: parsed listings plus athome's own paging counts."""

    listings: list[ListingCreate] = field(default_factory=list)
    total: int = 0          # total results athome reports for this search
    total_pages: int = 0    # paginator.totalPages
    rows_on_page: int = 0    # raw entries in search.list before our parsing


class AtHomeScraper(LuxBaseScraper):
    SOURCE_NAME = "athome"
    BASE_URL = "https://www.athome.lu"

    def search_url(self, commune: str, listing_type: str, page: int = 1) -> str:
        """SERP URL using athome's real location filter (q=<hkey>).

        ``commune`` is a target-commune name; its hkey is looked up in
        COMMUNE_HKEYS. ``loc=`` is cosmetic (athome ignores it) but kept so the
        URL is human-readable in logs.
        """
        params = TRANSACTION_PARAMS.get(listing_type, TRANSACTION_PARAMS["buy"])
        hkey = COMMUNE_HKEYS.get(commune, "")
        slug = commune.lower().replace(" ", "-")
        return f"{self.BASE_URL}/srp/?{params}&q={hkey}&loc=L7-{slug}&page={page}"

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
        """Back-compatible: just the listings from a SERP page."""
        return cls.parse_serp_page(html, listing_type).listings

    @classmethod
    def parse_serp_page(cls, html: str, listing_type: str) -> PageResult:
        """Parse a SERP page into listings + athome's paging counts."""
        state = cls._extract_initial_state(html)
        if not state:
            return PageResult()
        search = state.get("search") or {}
        details = search.get("list") or []
        meta_by_id = {m.get("id"): m for m in (search.get("listings") or [])}
        paginator = search.get("paginator") or {}

        result = PageResult(
            total=_to_int(search.get("total")) or 0,
            total_pages=_to_int(paginator.get("totalPages")) or 0,
            rows_on_page=len(details),
        )
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
                result.listings.append(listing)
        return result

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
        # Sale vs rental comes from the SEARCH (buy vs rent). Within rentals,
        # furnished is detected from the listing text (athome has no working
        # furnished URL facet, and its per-entry hasFurnished is always -1/None).
        is_sale = listing_type == "buy"
        if is_sale:
            lt = "buy"
        else:
            descs_all = entry.get("descriptions") or {}
            blob = " ".join(str(v) for v in descs_all.values()) + " " + str(entry.get("description") or "")
            lt = "furnished" if (_FURNISHED_RE.search(blob) and not _NOT_FURNISHED_RE.search(blob)) else "rent"
        # Rentals must have a price (we score on it); sales are often
        # "price on request" — keep those (price stays None).
        if not is_sale and (meta.get("isPriceOnDemand") or not price):
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
            photos_urls=[],  # we intentionally don't store photos — keep the advert link only
        )
        if lt == "buy":
            fields["price_eur"] = price  # may be None (price on request)
        else:  # rent or furnished -> a rental price (guaranteed non-None above)
            fields["rent_eur"] = price
        return ListingCreate(**fields)

    @staticmethod
    def _commune_from_geo(geo: dict) -> str | None:
        """Map athome's cityName (e.g. 'Luxembourg-Belair') to a target commune."""
        city = (geo.get("cityName") or "").strip()
        for name in TARGET_COMMUNES:
            if city == name or city.startswith(f"{name}-") or city.startswith(f"{name} "):
                return name
        return city or None

    # Searches to run (furnished is derived from the rent search, not its own).
    CATEGORIES = ("rent", "buy")

    async def _get(self, client, url: str, *, tries: int = 4):
        """GET with backoff retry (the workstation link can be flaky)."""
        import asyncio

        import httpx

        delay = 2.0
        for attempt in range(1, tries + 1):
            try:
                return await client.get(url)
            except httpx.TransportError as exc:
                if attempt == tries:
                    self.logger.warning("athome: giving up on %s (%s)", url, type(exc).__name__)
                    return None
                self.logger.info("athome: retry %d/%d for %s (%s)", attempt, tries, url, type(exc).__name__)
                await asyncio.sleep(delay)
                delay *= 2
        return None

    async def scrape(self) -> list[ListingCreate]:
        """Live scrape: every category × target commune, server-side filtered by
        the commune hkey and paginated fully. Logs a funnel line per commune so a
        thin harvest is immediately diagnosable.
        """
        import httpx

        results: list[ListingCreate] = []
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "fr-LU,fr;q=0.9"}
        # hard ceiling per (category, commune) so a bad filter can't run away.
        max_pages = max(1, min(self.max_pages, 50))

        timeout = httpx.Timeout(60.0, connect=15.0)
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout) as client:
            for category in self.CATEGORIES:
                for commune in self.communes():
                    if commune not in COMMUNE_HKEYS:
                        self.logger.warning("athome: no hkey for %r — skipping", commune)
                        continue
                    kept = fetched_rows = reported_total = 0
                    pages_seen = 0
                    page = 1
                    while page <= max_pages:
                        url = self.search_url(commune, category, page)
                        resp = await self._get(client, url)
                        if resp is None or resp.status_code != 200:
                            if page == 1:
                                self.logger.warning(
                                    "athome: %s/%s page 1 -> %s", category, commune,
                                    "no response" if resp is None else resp.status_code,
                                )
                            break
                        pr = self.parse_serp_page(resp.text, category)
                        reported_total = pr.total
                        fetched_rows += pr.rows_on_page
                        results.extend(pr.listings)
                        kept += len(pr.listings)
                        pages_seen += 1
                        last_page = min(pr.total_pages or 1, max_pages)
                        if pr.rows_on_page == 0 or page >= last_page:
                            break
                        page += 1
                        await self.random_delay()
                    # Funnel line: what athome had vs. what we parsed.
                    self.logger.info(
                        "athome funnel %-9s %-12s total=%-5s pages=%-2d rows=%-4d kept=%d",
                        category, commune, reported_total, pages_seen, fetched_rows, kept,
                    )
        self.logger.info("athome: %d listings parsed across all searches", len(results))
        return results
