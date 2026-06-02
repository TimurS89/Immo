"""Luxembourg-specific, non-negotiable configuration (hard inputs).

Single source of truth for the LU market:
- the DWS office (commute origin/destination) + the rush-hour departure rule,
- the target communes (with scoring/routing metadata),
- the hard filters (rent + buy),
- the soft-scoring weights.

These are deliberately typed Python constants (not user-editable YAML) because the
spec marks them "non-negotiable". The LU entry is injected into the country
registry by ``src.config.load_config`` via :func:`lu_country_config`.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Locale / currency / portals -------------------------------------------------
LU_COUNTRY_CODE = "LU"
LU_CURRENCY = "EUR"
LU_LOCALE = "fr-LU"  # primary; de-LU and en also appear in listings
LU_TIMEZONE = "Europe/Luxembourg"
LU_PORTALS: tuple[str, ...] = ("athome", "immotop", "wortimmo")

# Only athome runs by default. immotop & wortimmo are PARKED: both are
# Cloudflare-walled (would need a headless browser) and largely duplicate athome's
# agency listings, so the effort-to-payoff is poor. Their scrapers stay in the
# codebase — move a name here to re-enable it.
ACTIVE_PORTALS: tuple[str, ...] = ("athome",)


# --- DWS office: the commute origin/destination ---------------------------------
@dataclass(frozen=True)
class Office:
    name: str
    address: str
    lat: float
    lng: float

    @property
    def coords(self) -> tuple[float, float]:
        return (self.lat, self.lng)


DWS_OFFICE = Office(
    name="DWS",
    address="2 Boulevard Konrad Adenauer, L-1115 Luxembourg",
    lat=49.6315,
    lng=6.1717,
)


# --- Target communes (search and filter scope) ----------------------------------
# lat/lng are the commune centre (a single anchor point) used only for the
# approximate drive/PT commute estimate to the office. Deliberately coarse — no
# per-listing geocoding or walking-distance precision.
TARGET_COMMUNES: dict[str, dict] = {
    "Luxembourg":    {"foreign_pct": 70, "has_train": True,  "primary": True,
                      "lat": 49.6116, "lng": 6.1319,
                      "appeal": "capital; office (Kirchberg) in-commune, transport hub, all amenities, expat-dense"},
    "Strassen":      {"foreign_pct": 65, "has_train": False, "primary": True,
                      "lat": 49.6206, "lng": 6.0747,
                      "appeal": "closest to office, highest expat %, premium pricing"},
    "Bertrange":     {"foreign_pct": 55, "has_train": False, "primary": True,
                      "lat": 49.6092, "lng": 6.0530,
                      "appeal": "close to office, expat-dense, Belle Étoile shopping"},
    "Mamer":         {"foreign_pct": 54, "has_train": True,  "primary": True,
                      "lat": 49.6278, "lng": 6.0228,
                      "appeal": "European School, family, train station"},
    "Walferdange":   {"foreign_pct": 54, "has_train": True,  "primary": True,
                      "lat": 49.6586, "lng": 6.1306,
                      "appeal": "family, forest, train, schools"},
    "Hesperange":    {"foreign_pct": 55, "has_train": False, "primary": False,
                      "lat": 49.5728, "lng": 6.1542,
                      "appeal": "south of the city, expat-friendly"},
    "Leudelange":    {"foreign_pct": 45, "has_train": False, "primary": False,
                      "lat": 49.5667, "lng": 6.0883,
                      "appeal": "near Cloche d'Or business district, quiet, quick to the city"},
}

PRIMARY_COMMUNES: tuple[str, ...] = tuple(
    name for name, meta in TARGET_COMMUNES.items() if meta["primary"]
)

# athome.lu commune-level location-filter tokens ("hkey"), captured from the
# site's resolved search URLs (the ``q=<hkey>`` parameter — ``loc=`` alone is
# ignored). These are stable, transaction-independent geographic IDs (the same
# token filters rent, buy and furnished). The scraper logs the per-commune result
# count, so if athome ever rebuilds its geo index a stale token shows up as
# total=0 and we re-capture it.
COMMUNE_HKEYS: dict[str, str] = {
    "Luxembourg":  "d8380e34",
    "Strassen":    "e7677861",
    "Bertrange":   "4d6066a8",
    "Mamer":       "a8916871",
    "Walferdange": "a2e51548",
    "Hesperange":  "7d5d258f",
    "Leudelange":  "1b11c8fe",
}


# --- Hard filters (apply to all three listing categories) ------------------------
# Deliberately simple: rooms, surface, commune — plus a per-type price ceiling.
# Commute is NOT a knockout (soft indicator only); there is no minimum price.
HARD_FILTERS: dict = {
    "min_rooms": 3,       # pièces if the portal reports them, else bedrooms
    "max_rooms": 8,
    "min_surface_m2": 80,
    "communes": list(TARGET_COMMUNES),
}

# Per-listing-type maximum price (EUR). buy is a sale price; rent is the monthly
# total; furnished is intentionally uncapped (None). A missing/unknown price never
# fails the filter — only a price strictly above the cap does.
MAX_PRICE_EUR: dict = {
    "buy": 3_000_000,
    "rent": 6_000,
    "furnished": None,
}

# Furnished / long-term rent / buy share the room/surface/commune criteria; the
# price ceiling differs per type (looked up via MAX_PRICE_EUR in scoring).
HARD_FILTERS_RENT = HARD_FILTERS
HARD_FILTERS_BUY = HARD_FILTERS

# --- Soft scoring weights (post-filter ranking; must sum to 100) -----------------
SCORING_WEIGHTS: dict = {
    "drive_time": 25,        # approximate car commute to the office (indicator)
    "pt_time": 20,           # approximate public-transport commute (indicator)
    "foreign_pct": 15,       # expat-friendliness of the commune
    "energy_class": 10,
    "has_garage": 8,
    "has_garden": 8,
    "llm_quality_score": 14,  # description quality (heuristic flags/highlights)
}


def lu_country_config():
    """Build the Luxembourg :class:`CountryConfig` for the country registry.

    Search ``areas`` are the target communes; ``regions`` mirrors their names.
    Imported lazily to avoid a circular import with ``src.config``.
    """
    from src.config import CountryConfig, PortalConfig, SearchArea

    areas = [
        SearchArea(name=name, city=name, regions=[name]) for name in TARGET_COMMUNES
    ]
    return CountryConfig(
        enabled=True,
        currency=LU_CURRENCY,
        locale=LU_LOCALE,
        timezone=LU_TIMEZONE,
        regions=list(TARGET_COMMUNES),
        areas=areas,
        portals=[
            PortalConfig(name=p, enabled=(p in ACTIVE_PORTALS)) for p in LU_PORTALS
        ],
    )
