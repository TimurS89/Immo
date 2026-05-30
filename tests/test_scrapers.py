"""Basic tests for scraper infrastructure."""

import pytest
from unittest.mock import MagicMock
from src.config import (
    AppConfig,
    CountryConfig,
    Filters,
    PortalConfig,
    ScrapersConfig,
    SearchArea,
)
from src.scrapers.base import BaseScraper, PropertyData


class DummyScraper(BaseScraper):
    SOURCE_NAME = "test"
    COUNTRY = "DE"
    BASE_URL = "https://example.com"

    async def scrape(self):
        return [
            PropertyData(
                external_id="123",
                source=self.SOURCE_NAME,
                country="DE",
                listing_type="buy",
                property_type="apartment",
                title="Test Apartment",
                price=250000,
                rooms=3,
                living_area_sqm=80,
                listing_url="https://example.com/123",
            )
        ]


def make_config():
    return AppConfig(
        search_areas={
            "DE": CountryConfig(
                enabled=True,
                locale="de-DE",
                timezone="Europe/Berlin",
                areas=[SearchArea(name="Baden-Baden", city="Baden-Baden", postal_codes=["76530"])],
                portals=[PortalConfig(name="immoscout24"), PortalConfig(name="immowelt")],
            ),
            "FR": CountryConfig(
                enabled=False,  # disabled by default
                locale="fr-FR",
                timezone="Europe/Paris",
                areas=[SearchArea(name="Alsace", departments=["67", "68"])],
                portals=[PortalConfig(name="leboncoin")],
            ),
        },
        filters_buy=Filters(max_price=1000000, min_rooms=3, min_area_sqm=50),
        filters_rent=Filters(max_price=2500, min_rooms=3, min_area_sqm=50),
        scrapers=ScrapersConfig(request_delay_seconds=[0, 0]),
    )


def test_property_data_creation():
    p = PropertyData(
        external_id="abc",
        source="test",
        country="DE",
        listing_type="buy",
        property_type="apartment",
        title="Test",
    )
    assert p.external_id == "abc"
    assert p.price is None
    assert p.image_urls == []


def test_get_filters():
    config = make_config()
    scraper = DummyScraper(config)
    buy_filters = scraper.get_filters("buy")
    assert buy_filters.max_price == 1000000
    rent_filters = scraper.get_filters("rent")
    assert rent_filters.max_price == 2500


def test_get_search_areas():
    config = make_config()
    scraper = DummyScraper(config)
    areas = scraper.get_search_areas()
    assert len(areas) == 1
    assert areas[0].city == "Baden-Baden"


@pytest.mark.asyncio
async def test_dummy_scraper():
    config = make_config()
    scraper = DummyScraper(config)
    results = await scraper.scrape()
    assert len(results) == 1
    assert results[0].price == 250000
