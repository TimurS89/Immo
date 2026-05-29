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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# --- Locale / currency / portals -------------------------------------------------
LU_COUNTRY_CODE = "LU"
LU_CURRENCY = "EUR"
LU_LOCALE = "fr-LU"  # primary; de-LU and en also appear in listings
LU_TIMEZONE = "Europe/Luxembourg"
LU_PORTALS: tuple[str, ...] = ("athome", "immotop", "wortimmo")


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


def next_rush_hour_departure(now: datetime | None = None) -> datetime:
    """Next Tuesday 08:00 in Europe/Luxembourg — *actual* peak, not discounted.

    Used as the ``departure_time`` for drive/PT routing (Phase 4). If today is
    Tuesday before 08:00, that's the result; otherwise the upcoming Tuesday.
    """
    tz = ZoneInfo(LU_TIMEZONE)
    now = now.astimezone(tz) if now is not None else datetime.now(tz)
    days_ahead = (1 - now.weekday()) % 7  # Tuesday == weekday() 1
    candidate = now.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(
        days=days_ahead
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


# --- Target communes (search and filter scope) ----------------------------------
# NOTE: school_lat/school_lng are reasonable starting points for the main
# école fondamentale of each commune; Phase 4 (routing.py) re-geocodes them
# precisely via Google before computing walking times. For Ville de Luxembourg
# (many schools across quarters) the coordinate is a central placeholder.
TARGET_COMMUNES: dict[str, dict] = {
    "Luxembourg":    {"foreign_pct": 70, "has_train": True,  "primary": True,
                      "school_lat": 49.6117, "school_lng": 6.1250,
                      "appeal": "capital; office (Kirchberg) in-commune, transport hub, all amenities, expat-dense"},
    "Walferdange":   {"foreign_pct": 54, "has_train": True,  "primary": True,
                      "school_lat": 49.6584, "school_lng": 6.1306,
                      "appeal": "family, forest, train, school cluster strong"},
    "Bertrange":     {"foreign_pct": 55, "has_train": False, "primary": True,
                      "school_lat": 49.6094, "school_lng": 6.0670,
                      "appeal": "close to office, expat-dense, shopping"},
    "Strassen":      {"foreign_pct": 65, "has_train": False, "primary": True,
                      "school_lat": 49.6193, "school_lng": 6.0815,
                      "appeal": "closest to office, highest expat %, premium pricing"},
    "Mamer":         {"foreign_pct": 54, "has_train": True,  "primary": True,
                      "school_lat": 49.6300, "school_lng": 6.0235,
                      "appeal": "European School, family, train station"},
    "Hesperange":    {"foreign_pct": 55, "has_train": False, "primary": False,
                      "school_lat": 49.5757, "school_lng": 6.1531,
                      "appeal": "south of city, expat-friendly"},
    "Sandweiler":    {"foreign_pct": 45, "has_train": False, "primary": False,
                      "school_lat": 49.6147, "school_lng": 6.2050,
                      "appeal": "close to office and airport"},
    "Howald":        {"foreign_pct": 55, "has_train": False, "primary": False,
                      "school_lat": 49.5938, "school_lng": 6.1338,
                      "appeal": "edge of city, well-connected"},
}

PRIMARY_COMMUNES: tuple[str, ...] = tuple(
    name for name, meta in TARGET_COMMUNES.items() if meta["primary"]
)


# --- Hard filters (rentals — primary use case) ----------------------------------
HARD_FILTERS_RENT: dict = {
    "min_bedrooms": 4,
    "min_surface_m2": 100,
    "min_rent_total_eur": 2500,
    "max_rent_total_eur": 4500,  # inclusive of charges
    "max_drive_time_rush_min": 30,  # RUSH HOUR, Tuesday 08:00
    "max_pt_time_rush_min": 60,  # RUSH HOUR, Tuesday 08:00
    "communes": list(TARGET_COMMUNES),
}

# --- Hard filters (sale — secondary, lower priority initially) -------------------
HARD_FILTERS_BUY: dict = {
    "min_bedrooms": 4,
    "min_surface_m2": 100,
    "min_price_eur": 800_000,
    "max_price_eur": 1_400_000,
    "max_drive_time_rush_min": 30,
    "max_pt_time_rush_min": 60,
    "communes": list(TARGET_COMMUNES),
}

# --- Soft scoring weights (post-filter ranking) ---------------------------------
SCORING_WEIGHTS: dict = {
    "drive_time": 20,
    "pt_time": 15,
    "foreign_pct": 10,
    "school_walking_distance": 15,
    "creche_walking_distance": 5,
    "park_walking_distance": 5,
    "energy_class": 10,
    "has_garage": 5,
    "has_garden": 5,
    "ground_floor_with_garden": 3,
    "llm_quality_score": 7,
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
        portals=[PortalConfig(name=p) for p in LU_PORTALS],
    )
