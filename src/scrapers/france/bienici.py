"""Bien'ici scraper using reverse-engineered JSON API."""

from __future__ import annotations

import json
import re

import httpx

from src.scrapers.base import BaseScraper, PropertyData

# Bien'ici API endpoint
API_URL = "https://www.bienici.com/realEstateAds.json"

PROPERTY_TYPE_MAP = {
    "apartment": "flat",
    "house": "house",
    "land": "land",
}

TRANSACTION_MAP = {
    "buy": "buy",
    "rent": "rent",
}

# Approximate bounding boxes for Alsace departments
ALSACE_ZONES = {
    "67": {  # Bas-Rhin
        "zoneIdsByTypes": {"departement": ["departement-67"]},
    },
    "68": {  # Haut-Rhin
        "zoneIdsByTypes": {"departement": ["departement-68"]},
    },
}


class BienIciScraper(BaseScraper):
    SOURCE_NAME = "bienici"
    COUNTRY = "FR"
    BASE_URL = "https://www.bienici.com"

    async def scrape(self) -> list[PropertyData]:
        results = []

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Referer": "https://www.bienici.com/recherche/achat/alsace",
        }

        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
            for listing_type in ["buy", "rent"]:
                filters = self.get_filters(listing_type)
                for prop_type in filters.property_types:
                    for area in self.get_search_areas():
                        for dept in area.departments:
                            try:
                                listings = await self._fetch_listings(
                                    client, listing_type, prop_type, filters, dept
                                )
                                results.extend(listings)
                            except Exception:
                                self.logger.exception(
                                    f"Error scraping {listing_type}/{prop_type} dept {dept}"
                                )

        return results

    async def _fetch_listings(
        self, client, listing_type, property_type, filters, department
    ) -> list[PropertyData]:
        results = []
        zone = ALSACE_ZONES.get(department, {})

        for page_num in range(1, self.max_pages + 1):
            params = {
                "filters": json.dumps({
                    "size": 24,
                    "from": (page_num - 1) * 24,
                    "filterType": TRANSACTION_MAP.get(listing_type, "buy"),
                    "propertyType": [PROPERTY_TYPE_MAP.get(property_type, "flat")],
                    "maxPrice": int(filters.max_price),
                    "minRooms": int(filters.min_rooms),
                    "minArea": int(filters.min_area_sqm),
                    **zone,
                }),
            }

            self.logger.info(f"Fetching Bien'ici API page {page_num} dept {department}")
            resp = await client.get(API_URL, params=params)

            if resp.status_code != 200:
                self.logger.warning(f"HTTP {resp.status_code} from Bien'ici API")
                # Try alternative search URL approach
                listings = await self._fetch_via_search_page(
                    client, listing_type, property_type, filters, department, page_num
                )
                results.extend(listings)
                if not listings:
                    break
                continue

            await self.random_delay()
            data = resp.json()
            ads = data.get("realEstateAds", [])

            if not ads:
                break

            for ad in ads:
                prop = self._parse_ad(ad, listing_type, property_type)
                if prop:
                    results.append(prop)

            self.logger.info(f"Page {page_num}: {len(ads)} listings")

            total = data.get("total", 0)
            if page_num * 24 >= total:
                break

        return results

    async def _fetch_via_search_page(
        self, client, listing_type, property_type, filters, department, page_num
    ) -> list[PropertyData]:
        """Fallback: scrape the search page HTML for embedded JSON data."""
        results = []
        transaction = "achat" if listing_type == "buy" else "location"
        dept_name = "bas-rhin" if department == "67" else "haut-rhin"

        url = (
            f"{self.BASE_URL}/recherche/{transaction}/{dept_name}"
            f"?prix-max={int(filters.max_price)}"
            f"&nb-pieces-min={int(filters.min_rooms)}"
            f"&surface-min={int(filters.min_area_sqm)}"
            f"&page={page_num}"
        )

        resp = await client.get(url)
        if resp.status_code != 200:
            return results

        # Try to extract embedded JSON from the HTML
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?});', resp.text, re.DOTALL)
        if not match:
            match = re.search(r'"realEstateAds"\s*:\s*(\[.+?\])', resp.text, re.DOTALL)

        if match:
            try:
                data = json.loads(match.group(1))
                ads = data if isinstance(data, list) else data.get("realEstateAds", [])
                for ad in ads:
                    prop = self._parse_ad(ad, listing_type, property_type)
                    if prop:
                        results.append(prop)
            except json.JSONDecodeError:
                pass

        return results

    def _parse_ad(
        self, ad: dict, listing_type: str, property_type: str
    ) -> PropertyData | None:
        ext_id = str(ad.get("id", ""))
        if not ext_id:
            return None

        title = ad.get("title", "")
        if not title:
            city = ad.get("city", "")
            rooms = ad.get("roomsQuantity", "")
            title = f"{property_type.capitalize()} {rooms} pièces - {city}"

        price = ad.get("price")
        rooms = ad.get("roomsQuantity")
        area = ad.get("surfaceArea")
        plot = ad.get("landSurfaceArea")

        city = ad.get("city", "")
        postal = str(ad.get("postalCode", ""))
        district = ad.get("district", {})
        lat = ad.get("blurredLatitude", ad.get("latitude"))
        lon = ad.get("blurredLongitude", ad.get("longitude"))

        energy = ad.get("energyClassification", ad.get("energyValue", ""))

        photos = []
        for photo in ad.get("photos", []):
            if isinstance(photo, str):
                photos.append(photo)
            elif isinstance(photo, dict):
                photos.append(photo.get("url", photo.get("url_photo", "")))

        description = ad.get("description", "")

        # Features
        has_balcony = ad.get("hasBalcony", False)
        has_garden = ad.get("hasGarden", False)
        has_garage = ad.get("hasGarage", False) or ad.get("hasParkingSpace", False)
        has_elevator = ad.get("hasElevator", False)
        floor_val = ad.get("floor")
        year_built = ad.get("yearOfConstruction")

        return PropertyData(
            external_id=ext_id,
            source=self.SOURCE_NAME,
            country="FR",
            listing_type=listing_type,
            property_type=property_type,
            title=title,
            description=description,
            price=float(price) if price else None,
            rooms=float(rooms) if rooms else None,
            living_area_sqm=float(area) if area else None,
            plot_area_sqm=float(plot) if plot else None,
            address_city=city,
            address_postal_code=postal,
            latitude=float(lat) if lat else None,
            longitude=float(lon) if lon else None,
            energy_rating=str(energy) if energy else None,
            year_built=int(year_built) if year_built else None,
            floor=int(floor_val) if floor_val else None,
            has_balcony=has_balcony,
            has_garden=has_garden,
            has_garage=has_garage,
            has_elevator=has_elevator,
            image_urls=photos[:10],
            listing_url=f"{self.BASE_URL}/annonce/{ext_id}",
            raw_data=ad,
        )
