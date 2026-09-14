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

"Offered" is not "fitted": a feature the advert proposes to install
("possibilité d'installer une climatisation", "climatisation en option") reads
as ``None``, not ``True`` — see ``_HYPOTHETICAL_RE``.

The bias throughout is that **no answer beats a wrong one**. A plot size lifted
off the wrong number looks entirely plausible in the dashboard and there is
nothing downstream to catch it, so the patterns refuse anything they can't tie
to the plot phrase itself (see ``_PLOT_STOP_RE``, and the cases pinned in
``tests/test_features.py``).

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


# A feature the advert merely *offers to install* is not a feature the property
# has — "possibilité d'installer une climatisation" and "climatisation en option"
# both describe something absent today. Treating those as present is a claim the
# advert never made, so a cue anywhere in the same sentence downgrades the match
# to unknown (the honest answer: it's neither fitted nor ruled out).
_HYPOTHETICAL_RE = re.compile(
    r"\b(possibilite|possibilites|possible|en option|optionnel(?:le)?|optional|"
    r"pre[\s-]?equipe[e]?s?|prevu[es]?\s+pour|prepare[es]?\s+pour|preparation|"
    r"peut\s+etre\s+(?:installe|ajoute)|sur\s+demande|en\s+supplement|"
    r"moglichkeit|vorbereitet|vorgesehen|auf\s+wunsch|gegen\s+aufpreis|"
    r"can\s+be\s+(?:installed|added)|on\s+request)\b"
)


def _sentence_around(norm: str, index: int) -> str:
    """The sentence containing ``index`` — cues don't carry across a full stop."""
    start = norm.rfind(".", 0, index) + 1
    end = norm.find(".", index)
    return norm[start : end if end != -1 else len(norm)]


def _tri_state(text: str, positive: re.Pattern[str], negative: re.Pattern[str]) -> bool | None:
    """True if mentioned, False if explicitly negated, None if not mentioned.

    A mention wrapped in a "could be installed" / "available as an option" phrase
    counts as *not mentioned* rather than present.
    """
    norm = normalize_text(text)
    if negative.search(norm):
        return False
    for match in positive.finditer(norm):
        if not _HYPOTHETICAL_RE.search(_sentence_around(norm, match.start())):
            return True  # at least one unconditional mention
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
#
# `m²` is deliberately absent from the unit list: NFKD normalization decomposes
# U+00B2 to "2", so by the time these patterns run "m²" is already "m2".
_NUM = r"(?P<num>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_UNIT = r"(?P<unit>ares?|ar|m2|sqm|qm)"

# Bare "land" is NOT a keyword. German "Land" means country/region
# ("im Luxemburger Land, 120 m2 Wohnfläche") and was measured returning the
# LIVING area as the plot — a wrong number that looks entirely plausible. The
# English noun only counts in its explicit "land of / land area" forms.
# German appears both with umlauts (NFKD-stripped to "grundstucksflache") and
# transliterated ("Grundstuecksflaeche") — athome carries both spellings.
_PLOT_KEYWORD = (
    r"(?:terrains?|grundstu(?:e)?ck(?:e|s)?(?:fl(?:a|ae)che)?|bauland|parcelle|"
    r"plot|land\s+(?:of|area|size))"
)

# Once the sentence has moved on to the dwelling or its living area, a following
# number is no longer the plot: "sur terrain clôturé, maison de 180 m²" is a
# house of 180 m² on a plot of unstated size, not a 180 m² plot.
_PLOT_STOP_RE = re.compile(
    r"\b(maison|appartement|logement|villa|haus|wohnung|house|apartment|flat|"
    r"habitable[s]?|wohnflache|living|sejour|surface\s+habitable)\b"
)

_LAND_PATTERNS = (
    # keyword first: the gap may not cross a comma or full stop — that is where
    # the plot phrase ends and a new subject begins.
    re.compile(rf"\b{_PLOT_KEYWORD}\b(?P<gap>[^.,;\n]{{0,25}}?){_NUM}\s*{_UNIT}\b"),
    # number first: "5 ares de terrain", "800 sqm plot".
    re.compile(rf"{_NUM}\s*{_UNIT}\s*(?:de\s+|of\s+|von\s+)?\b{_PLOT_KEYWORD}\b"),
)

# "terrain d'env. 5 ares" / "Grundstück ca. 800 m²": the gap above stops at a
# full stop, so an approximation abbreviation would hide the size behind its own
# period. Drop the dot before matching — these are the only periods in a plot
# phrase that don't end the sentence.
_ABBREV_DOT_RE = re.compile(r"\b(env|ca|approx|approximately|circa|abt|ungef)\.")


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
    norm = _ABBREV_DOT_RE.sub(r"\1", normalize_text(text))
    if not norm:
        return None
    for pattern in _LAND_PATTERNS:
        # finditer, not search: a candidate rejected for reaching past the plot
        # phrase must not hide a good one later in the same description.
        for match in pattern.finditer(norm):
            if _PLOT_STOP_RE.search(match.groupdict().get("gap") or ""):
                continue
            value = _parse_number(match.group("num"))
            if value is None:
                continue
            m2 = value * M2_PER_ARE if match.group("unit").startswith("ar") else value
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


# Matches the property_subtype column width (models.py). An unrecognised label
# slugifies through, so nothing stops a portal handing us a whole sentence.
SUBTYPE_MAX_LEN = 64


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", normalize_text(label)).strip("_")[:SUBTYPE_MAX_LEN].rstrip("_")


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
