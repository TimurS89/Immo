"""Structured property features derived from a listing's free text.

Distinct from ``analysis.py``: that module judges a listing (quality score, red
flags, highlights) as a post-persist pipeline stage. This module *extracts
structured attributes* at parse time — land size, air conditioning, solar panels
— for fields the portal's payload does not expose.

athome's search payload carries no land/AC/solar field (verified against the live
``search.list`` keys), so these are text heuristics. They are deliberately
tri-state:

* ``True``  — the text says the feature is present,
* ``False`` — the text explicitly negates it ("sans climatisation"),
* ``None``  — the text is silent, i.e. **unknown** (the common case — most ads
  don't enumerate every feature). Never conflate "not mentioned" with "absent".

Portal-agnostic: takes text, returns values. FR / DE / EN, accent-insensitive.
"""

from __future__ import annotations

import re
import unicodedata

# Luxembourg quotes land in ARES as often as m². 1 are = 100 m².
M2_PER_ARE = 100.0

# Sanity bounds for a plot (m²) — outside this it's a mis-parse, not a plot.
LAND_MIN_M2 = 20.0
LAND_MAX_M2 = 100_000.0  # 10 ha


def normalize_text(text: str | None) -> str:
    """Lowercase, strip diacritics, collapse whitespace (match 'rénové'≡'renove')."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped)


# --- feature keywords -------------------------------------------------------
# Matched on normalized text, so accents are already stripped from the patterns.
_AC_RE = re.compile(
    r"\b(climatisation|climatise[e]?s?|air\s*conditionn?e[e]?|klimaanlage|"
    r"klimatisiert|air[\s-]?conditioning|air[\s-]?conditioned)\b"
)
_AC_NEG_RE = re.compile(r"\b(sans|pas de|no|ohne|keine?)\s+(climatisation|klimaanlage|air[\s-]?conditioning)\b")

_SOLAR_RE = re.compile(
    r"\b(panneaux?\s+(solaires?|photovoltaiques?)|photovoltaique[s]?|"
    r"solaranlage|photovoltaik|solarpanel[e]?|solar\s*panels?|"
    r"pv[\s-]?anlage|installation\s+solaire)\b"
)
_SOLAR_NEG_RE = re.compile(
    r"\b(sans|pas de|no|ohne|keine?)\s+(panneaux?\s+solaires?|photovoltaique|solaranlage|solar\s*panels?)\b"
)

_HEAT_PUMP_RE = re.compile(
    r"\b(pompe\s+a\s+chaleur|warmepumpe|luftwarmepumpe|erdwarmepumpe|heat\s*pump|"
    r"pac\s+air[\s-]?eau|geothermie|geothermal)\b"
)
_HEAT_PUMP_NEG_RE = re.compile(
    r"\b(sans|pas de|no|ohne|keine?)\s+(pompe\s+a\s+chaleur|warmepumpe|heat\s*pump)\b"
)


def _tri_state(text: str, positive: re.Pattern[str], negative: re.Pattern[str]) -> bool | None:
    """True if mentioned, False if explicitly negated, None if not mentioned."""
    norm = normalize_text(text)
    if negative.search(norm):
        return False
    if positive.search(norm):
        return True
    return None


def detect_air_conditioning(text: str | None) -> bool | None:
    """Tri-state air-conditioning flag from FR/DE/EN description text."""
    return _tri_state(text or "", _AC_RE, _AC_NEG_RE)


def detect_solar_panels(text: str | None) -> bool | None:
    """Tri-state solar/photovoltaic flag from FR/DE/EN description text."""
    return _tri_state(text or "", _SOLAR_RE, _SOLAR_NEG_RE)


def detect_heat_pump(text: str | None) -> bool | None:
    """Tri-state heat-pump flag — the text fallback for athome's ``energy.heat_pump``.

    Most entries carry the structured flag; this covers the ones that don't.
    """
    return _tri_state(text or "", _HEAT_PUMP_RE, _HEAT_PUMP_NEG_RE)


# --- land / plot size -------------------------------------------------------
# "terrain de 5,5 ares", "5 ares de terrain", "Grundstück von 6 Ar",
# "terrain 500 m2", "plot of 800 sqm". Unit is captured so ares -> m² converts.
_NUM = r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_UNIT = r"(ares?|ar|m2|m²|sqm|qm)"
_LAND_PATTERNS = (
    re.compile(rf"\b(?:terrain|terrains|grundstuck|grundstuecke?|plot|land|parcelle)\b[^.\n]{{0,30}}?{_NUM}\s*{_UNIT}\b"),
    re.compile(rf"{_NUM}\s*{_UNIT}\s*(?:de\s+)?(?:terrain|grundstuck|plot|land|parcelle)\b"),
)


def _parse_number(raw: str) -> float | None:
    """Parse a EU/EN number. '1.500'/'1,500' = thousands; '5,5'/'5.5' = decimal."""
    txt = raw.strip()
    # A single separator followed by exactly 3 digits is a thousands group.
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", txt):
        return float(re.sub(r"[.,]", "", txt))
    txt = txt.replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return None


def extract_land_m2(text: str | None) -> float | None:
    """Plot/land size in m² from description text, or None if not stated.

    Handles Luxembourg's "ares" (1 are = 100 m²) as well as m²/sqm. Returns None
    for values outside a plausible plot range rather than a nonsense number.
    """
    norm = normalize_text(text)
    if not norm:
        return None
    for pattern in _LAND_PATTERNS:
        match = pattern.search(norm)
        if not match:
            continue
        value = _parse_number(match.group(1))
        if value is None:
            continue
        unit = match.group(2)
        m2 = value * M2_PER_ARE if unit.startswith("ar") else value
        if LAND_MIN_M2 <= m2 <= LAND_MAX_M2:
            return round(m2, 1)
    return None


# --- property type ----------------------------------------------------------
# Coarse type (for filtering) + normalized subtype (for detail). athome gives a
# free-text label like "Detached house" / "Semi-detached" / "Penthouse"; we
# normalize it but never drop an unknown one — it slugifies through.
_SUBTYPE_ALIASES: dict[str, str] = {
    "detached house": "detached_house",
    "detached": "detached_house",
    "maison": "house",
    "semi-detached": "semi_detached",
    "semi detached": "semi_detached",
    "semi-detached house": "semi_detached",
    "terraced": "terraced_house",
    "terraced house": "terraced_house",
    "town house": "terraced_house",
    "townhouse": "terraced_house",
    "villa": "villa",
    "mansion": "villa",
    "bungalow": "bungalow",
    "farmhouse": "farmhouse",
    "castle": "castle",
    "chalet": "chalet",
    "apartment": "apartment",
    "flat": "apartment",
    "studio": "studio",
    "duplex": "duplex",
    "triplex": "triplex",
    "penthouse": "penthouse",
    "loft": "loft",
    "ground floor": "ground_floor_flat",
    "attic": "attic_flat",
    "bedroom": "room",
    "room": "room",
}

# Which coarse bucket a normalized subtype belongs to.
_HOUSE_SUBTYPES = {
    "house", "detached_house", "semi_detached", "terraced_house", "villa",
    "bungalow", "farmhouse", "castle", "chalet",
}
_APARTMENT_SUBTYPES = {
    "apartment", "studio", "duplex", "triplex", "penthouse", "loft",
    "ground_floor_flat", "attic_flat", "room",
}


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", normalize_text(label)).strip("_")


def classify_property(
    subtype_label: str | None, portal_group: str | None = None
) -> tuple[str | None, str | None]:
    """Return ``(property_type, property_subtype)``.

    ``property_type`` is the coarse bucket — ``house`` / ``apartment`` / ``other``
    — for filtering; ``property_subtype`` is the normalized fine-grained label
    (``detached_house``, ``penthouse``, …). An unrecognised label is slugified
    through rather than dropped, and the coarse type then falls back to the
    portal's own group (athome: ``house`` / ``flat``).
    """
    label = (subtype_label or "").strip()
    subtype = _SUBTYPE_ALIASES.get(normalize_text(label)) or (_slug(label) or None)

    if subtype in _HOUSE_SUBTYPES:
        return "house", subtype
    if subtype in _APARTMENT_SUBTYPES:
        return "apartment", subtype

    group = normalize_text(portal_group)
    if group == "house":
        return "house", subtype
    if group in {"flat", "apartment"}:
        return "apartment", subtype
    return ("other" if subtype else None), subtype
