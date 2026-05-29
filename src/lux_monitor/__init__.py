"""Luxembourg property monitor — data model package.

Convenience re-exports so callers can do e.g.::

    from src.lux_monitor import Listing, ListingCreate, ListingRead, session_scope
"""

from .db import (
    DEFAULT_DB_PATH,
    PROJECT_ROOT,
    database_url,
    get_engine,
    init_db,
    make_session_factory,
    session_scope,
)
from .models import (
    DESCRIPTION_LANGS,
    ENERGY_CLASSES,
    LISTING_TYPES,
    PORTALS,
    Base,
    Listing,
    PriceHistoryEntry,
)
from .schemas import (
    ListingBase,
    ListingCreate,
    ListingRead,
    PriceHistoryEntryRead,
)

__all__ = [
    # models
    "Base",
    "Listing",
    "PriceHistoryEntry",
    "PORTALS",
    "LISTING_TYPES",
    "DESCRIPTION_LANGS",
    "ENERGY_CLASSES",
    # schemas
    "ListingBase",
    "ListingCreate",
    "ListingRead",
    "PriceHistoryEntryRead",
    # db
    "get_engine",
    "init_db",
    "make_session_factory",
    "session_scope",
    "database_url",
    "DEFAULT_DB_PATH",
    "PROJECT_ROOT",
]
