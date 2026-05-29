"""Pure, dependency-light parsing helpers for Luxembourg listings (FR/DE/EN).

All functions are total (return ``None`` on failure) and side-effect free, so they
are unit-tested directly without a browser or network.
"""

from __future__ import annotations

import re

_NBSP = " "
_NUM_RE = re.compile(r"\d[\d.,\s ]*\d|\d")


def parse_decimal(text: str | None) -> float | None:
    """Parse a EU- or EN-formatted number to float, inferring the conventions.

    Handles '2.500' / '2 500' / '2,500' (=2500), '85,5' / '85.5' (=85.5),
    '1.234,56' (EU) and '1,234.56' (EN). Rule: when both separators appear, the
    rightmost is the decimal mark; when only one appears, a 3-digit trailing group
    is treated as a thousands separator, otherwise as a decimal mark.
    """
    if text is None:
        return None
    m = _NUM_RE.search(str(text))
    if not m:
        return None
    tok = m.group(0).replace(_NBSP, "").replace(" ", "")

    has_comma, has_dot = "," in tok, "." in tok
    if has_comma and has_dot:
        if tok.rfind(",") > tok.rfind("."):  # EU: 1.234,56
            tok = tok.replace(".", "").replace(",", ".")
        else:  # EN: 1,234.56
            tok = tok.replace(",", "")
    elif has_comma:
        intpart, _, frac = tok.rpartition(",")
        tok = tok.replace(",", "") if (intpart and len(frac) == 3) else tok.replace(",", ".")
    elif has_dot:
        intpart, _, frac = tok.rpartition(".")
        if intpart and len(frac) == 3:  # thousands: 2.800 / 850.000
            tok = tok.replace(".", "")
        # else keep as decimal (85.5)

    try:
        return float(tok)
    except ValueError:
        return None


def parse_int(text: str | None) -> int | None:
    val = parse_decimal(text)
    return int(val) if val is not None else None


def parse_surface_m2(text: str | None) -> float | None:
    """Parse a living-area value like '120 m²', 'surface habitable 85,5 m²'."""
    return parse_decimal(text)


def parse_postcode(text: str | None) -> str | None:
    """Extract a Luxembourg 4-digit postcode ('L-7220', '7220')."""
    if text is None:
        return None
    m = re.search(r"L-?\s?(\d{4})", str(text), re.IGNORECASE) or re.search(
        r"\b(\d{4})\b", str(text)
    )
    return m.group(1) if m else None


def parse_energy_class(text: str | None) -> str | None:
    """Extract a single A–I energy/thermal class letter (LU passeport énergétique)."""
    if text is None:
        return None
    m = re.search(r"\b([A-I])\b", str(text).upper())
    return m.group(1) if m else None


# Floor keywords across FR / DE / EN.
_GROUND_FLOOR = ("rez-de-chaussée", "rez de chaussée", "rdc", "erdgeschoss", "ground floor")
_BASEMENT = ("sous-sol", "souterrain", "untergeschoss", "basement")


def parse_floor(text: str | None) -> int | None:
    """Parse a floor value. 0 = ground floor, -1 = below ground."""
    if text is None:
        return None
    low = str(text).strip().lower()
    if any(k in low for k in _GROUND_FLOOR):
        return 0
    if any(k in low for k in _BASEMENT):
        return -1
    m = re.search(r"(-?\d+)", low)  # '1er étage', '2. Stock', 'floor 3'
    return int(m.group(1)) if m else None


def bedrooms_from_chambres_pieces(
    chambres: int | None, pieces: int | None
) -> int | None:
    """Bedrooms = 'chambres' when given; else infer from 'pièces' (pièces − 1).

    'pièces' counts the living room too, so a 4-pièces flat ~ 3 bedrooms. Never
    returns a negative number.
    """
    if chambres is not None:
        return chambres
    if pieces is not None:
        return max(pieces - 1, 0)
    return None


# --- Feature detection (keyword based, multilingual) ----------------------------
_GARAGE_KW = ("garage", "box", "emplacement intérieur", "parking intérieur")
_GARDEN_KW = ("jardin", "garten", "garden")
_BALCONY_TERRACE_KW = ("balcon", "terrasse", "balkon", "terrace", "balcony")
_ELEVATOR_KW = ("ascenseur", "aufzug", "lift", "elevator")


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(k in text for k in keywords)


def detect_features(text: str | None) -> dict:
    """Detect boolean amenities from a free-text blob (title + description)."""
    low = (text or "").lower()
    return {
        "has_garage": _has_any(low, _GARAGE_KW),
        "has_garden": _has_any(low, _GARDEN_KW),
        "has_balcony_terrace": _has_any(low, _BALCONY_TERRACE_KW),
        "has_elevator": True if _has_any(low, _ELEVATOR_KW) else None,  # tri-state
    }


def detect_lang(text: str | None) -> str:
    """Cheap language guess for a listing description (fr | de | en). Defaults fr."""
    low = (text or "").lower()
    de = sum(w in low for w in (" und ", " mit ", "wohnung", "zimmer", "schlafz", "küche"))
    en = sum(w in low for w in (" and ", " with ", "bedroom", "kitchen", "for rent"))
    fr = sum(w in low for w in (" et ", " avec ", "chambre", "cuisine", "à louer", "pièces"))
    best = max((fr, "fr"), (de, "de"), (en, "en"), key=lambda t: t[0])
    return best[1] if best[0] > 0 else "fr"
