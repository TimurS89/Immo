# ARCHITECTURE.md

> Snapshot of the **Immo** property-search automation tool as it exists today
> (originally built for **Baden-Baden, Germany + Alsace, France**).
> This document describes the *current* system. It is the input for
> `ADAPTATION_PLAN.md`, which describes how to retarget it to **Luxembourg**.

---

## 1. High-level overview

A single-process Python application that, on a weekly schedule:

1. **Scrapes** 9 real-estate portals (4 German, 5 French) for buy/rent listings.
2. **Normalizes** every listing into a common `PropertyData` shape and **upserts**
   it into a local SQLite database, tracking price history over time.
3. **Analyzes** the data: cross-portal de-duplication, market statistics, and
   price-change detection.
4. **Reports**: builds Plotly charts, renders an HTML report + PDF, and emails it
   via Gmail SMTP.
5. **Dashboard**: an on-demand Streamlit UI for browsing the same database.

Orchestrated by `src/main.py` (CLI). Everything is local and file-based; no
server, no external DB.

```
            ┌──────────────────────── src/main.py (orchestrator/CLI) ────────────────────────┐
            │                                                                                  │
 config ──► │  load_config ─► init_db ─► run_scrapers ─► deduplicate ─► generate_report ─► email│
 .env       │       │            │            │              │               │                  │
            └───────┼────────────┼────────────┼──────────────┼───────────────┼──────────────────┘
                    ▼            ▼            ▼              ▼               ▼
               src/config   src/database  src/scrapers   src/analysis    src/reports
               (Pydantic)   (SQLAlchemy)  (Playwright/   (thefuzz/       (Plotly/Jinja2/
                                           httpx)         statistics)     WeasyPrint/SMTP)
                                                                          src/dashboard (Streamlit)
```

---

## 2. Module structure & dependencies

### 2.1 Package layout

| Path | Responsibility | Portal/geography coupling |
|------|----------------|---------------------------|
| `src/main.py` | CLI entry point + pipeline orchestration; hard-coded **scraper registry** | Low (registry lists the 9 sources) |
| `src/config.py` | YAML + `.env` loader, Pydantic config models | **Medium** — `search_germany` / `search_france` are named fields |
| `src/database/models.py` | SQLAlchemy ORM models (4 tables) | **High** — `source` & `country` are fixed **Enums** |
| `src/database/db.py` | Engine + session singletons, SQLite WAL/pragmas | None (generic) |
| `src/scrapers/base.py` | `BaseScraper` ABC, `PropertyData` dataclass, `save_results()` upsert | **Medium** — `get_search_areas()` branches on `DE`/`FR` |
| `src/scrapers/browser.py` | Playwright stealth browser, proxy rotation, per-country locale | **Medium** — `LOCALES` map has only `DE`/`FR` |
| `src/scrapers/captcha.py` | Optional 2Captcha solver | None |
| `src/scrapers/germany/*` (4) | Portal-specific scrapers (ImmoScout24, Immowelt, Kleinanzeigen, Wohnungsbörse) | **Total** — site + region baked in |
| `src/scrapers/france/*` (5) | Portal-specific scrapers (LeBonCoin, SeLoger, Bien'ici, PAP, ParuVendu) | **Total** — site + region baked in |
| `src/analysis/deduplication.py` | Fuzzy cross-source duplicate detection | None (works on any country value) |
| `src/analysis/market_stats.py` | Aggregates → `MarketSnapshot` rows | None |
| `src/analysis/price_tracker.py` | Price-change detection from `PriceHistory` | None |
| `src/reports/generator.py` | Report orchestrator | **Medium** — hard-coded `(DE, "Germany (Baden-Baden)"), (FR, "France (Alsace)")` loop |
| `src/reports/charts.py` | Plotly chart builders | **Medium** — DE/FR subplot titles & colors hard-coded |
| `src/reports/pdf.py` | WeasyPrint HTML→PDF | None |
| `src/reports/email_sender.py` | Gmail SMTP + STARTTLS | None |
| `src/reports/templates/*` | Jinja2 HTML/PDF templates + CSS | **Low** — header strings say "Baden-Baden (DE) & Alsace (FR)" |
| `src/dashboard/app.py` | Streamlit browser UI | **Medium** — country selectbox & trend loop hard-code DE/FR |

### 2.2 Internal dependency direction

```
main ─► config, database, scrapers, analysis, reports
scrapers ─► config, database(models), browser, captcha
analysis ─► database(models)
reports  ─► config, database(models), analysis
dashboard─► config, database
```

- Clean, acyclic, layered. **`config` and `database.models` are the two shared
  foundations** that nearly everything imports.
- Scrapers are registered **lazily** (`main._register_scrapers()` imports them
  inside a function), so Playwright is only imported when scraping actually runs —
  tests and report-only runs don't need it.

### 2.3 Third-party dependencies (from `pyproject.toml` / `requirements.txt`)

| Area | Libraries |
|------|-----------|
| Scraping | `playwright`, `playwright-stealth` (1.x API: `stealth_async`), `httpx`, `beautifulsoup4`, `lxml` |
| Persistence | `sqlalchemy>=2.0`, `alembic` *(declared but unused — see §7)* |
| Config | `pyyaml`, `python-dotenv`, `pydantic>=2.5` |
| Analysis | `thefuzz[speedup]` (pulls `python-Levenshtein`), stdlib `statistics` |
| Reports | `jinja2`, `plotly`, `kaleido`, `weasyprint` |
| Dashboard | `streamlit` |
| Logging/CLI | `rich` |
| CAPTCHA (opt) | `twocaptcha-python` |
| Dev | `pytest`, `pytest-asyncio` |

---

## 3. Portal-specific vs. portal-agnostic code

This is the central question for adaptation. The codebase **already separates**
the two reasonably well, but geography leaks into a handful of agnostic layers.

### 3.1 Portal-agnostic core (no site knowledge)

- **`PropertyData`** dataclass (`scrapers/base.py`) — the canonical normalized shape.
- **`BaseScraper.save_results()`** — upsert + price-history + `ScrapeRun` logging.
  Site-independent; every scraper feeds it `list[PropertyData]`.
- **`database/`** — engine, session, models (the *table shapes*, not the enum values).
- **`analysis/`** — dedup, market stats, price tracking. Operate purely on rows.
- **`reports/pdf.py`, `reports/email_sender.py`** — pure transport/format.
- **`scrapers/captcha.py`** — generic.

### 3.2 Portal-specific code (one module per site)

- **`scrapers/germany/*` and `scrapers/france/*`** are *entirely* site-specific.
  Each subclass hard-codes three class attributes and the search strategy:

  ```python
  class ImmoweltScraper(BaseScraper):
      SOURCE_NAME = "immowelt"
      COUNTRY = "DE"
      BASE_URL = "https://www.immowelt.de"
  ```

  …plus **region baked into URL templates / zone maps**, e.g.:
  - `immowelt.py`: `"/liste/baden-baden/wohnungen/kaufen?..."` (city in the path)
  - `wohnungsboerse.py`: `"/immomarkt/baden-baden/..."`
  - `kleinanzeigen.py`: `"/.../baden-baden/c203l9377"` (location id)
  - `bienici.py`: `ALSACE_ZONES = {"67": ..., "68": ...}`
  - `leboncoin.py`: `ALSACE_LOCATIONS = {"67": "d_67", "68": "d_68"}`
  - `seloger.py`, `pap.py`: Alsace department → site location-id maps

  Two scraping techniques are used: **Playwright** (JS-heavy/protected sites) and
  **httpx + JSON/HTML parsing** (lighter sites). Both follow the same
  `scrape() -> list[PropertyData]` contract.

### 3.3 Leaky layers — agnostic in spirit but contain DE/FR literals

These are the friction points for a new country (counts from a repo sweep:
`"DE"`/`"FR"` literals appear in **15 files / ~41 sites**):

| File | Leak |
|------|------|
| `database/models.py` | `country` Enum = `("DE","FR")`; `source` Enum = the 9 site names |
| `config.py` | `AppConfig.search_germany` / `search_france`; `load_config` reads `search["germany"]`/`["france"]` |
| `scrapers/base.py` | `get_search_areas()`: `if self.COUNTRY == "DE": return search_germany else search_france` |
| `scrapers/browser.py` | `LOCALES = {"DE": de-DE/Berlin, "FR": fr-FR/Paris}` |
| `reports/generator.py` | `for country,label in [("DE","Germany (Baden-Baden)"),("FR","France (Alsace)")]` |
| `reports/charts.py` | DE/FR subplot titles, per-country colors |
| `dashboard/app.py` | `selectbox("Country", ["All","DE","FR"])`, DE/FR trend loop |
| `reports/templates/report.html`, `pdf_report.html` | header text "Baden-Baden (DE) & Alsace (FR)" |

---

## 4. Data model (current schema)

SQLite, 4 tables, defined in `src/database/models.py`. Schema is created with
`Base.metadata.create_all()` (no migrations — see §7).

### `properties` (main table)
- **Identity:** `id` (UUID str PK), `external_id`, `source` *(Enum, 9 values)*,
  `country` *(Enum `DE`/`FR`)*, `listing_type` *(Enum `rent`/`buy`)*,
  `property_type` *(Enum `apartment`/`house`/`land`)*.
- **Core facts:** `title`, `description`, `price`, `price_per_sqm` *(derived)*,
  `rooms`, `living_area_sqm`, `plot_area_sqm`.
- **Location:** `address_city`, `address_postal_code`, `address_street`,
  `latitude`, `longitude`.
- **Attributes:** `energy_rating` (free str), `year_built`, `floor`,
  `has_balcony/garden/garage/elevator`.
- **Media/contact:** `image_urls` (JSON), `listing_url`, `contact_info`.
- **Lifecycle:** `first_seen_at`, `last_seen_at`, `is_active`, `raw_data` (JSON),
  `created_at`, `updated_at`.
- **Indexes:** unique `(source, external_id)`; `(country, listing_type)`;
  `is_active`; `address_postal_code`.

### `price_history`
- `id` PK, `property_id` FK→properties (CASCADE), `price`, `recorded_at`.
- Written on insert and whenever a changed price is observed.

### `scrape_runs`
- `id` PK, `source`, `started_at`, `completed_at`, `listings_found`,
  `new_listings`, `updated_listings`, `errors`,
  `status` *(Enum running/success/partial/failed)*, `log`.

### `market_snapshots`
- `id` PK, `snapshot_date`, `country`, `listing_type`, `property_type`,
  `avg_price`, `median_price`, `avg_price_per_sqm`, `median_price_per_sqm`,
  `total_listings`, `new_listings_this_week`, `removed_listings_this_week`.
- Note: here `country`/`listing_type`/`property_type` are **plain strings**, not
  enums (unlike `properties`).

**Currency:** all monetary fields are EUR — convenient, since Luxembourg also uses
EUR (no currency work needed).

**Intermediate shape:** `PropertyData` (dataclass in `scrapers/base.py`) mirrors
`properties` minus the lifecycle/derived columns; it is the scraper→DB contract.

---

## 5. Filter & scoring logic

There is **filtering but no real "scoring"** in the current system.

### 5.1 Where filters live
- **Defined** in config as two `Filters` objects: `filters_buy` and `filters_rent`
  (`min_price`, `max_price`, `min_rooms`, `min_area_sqm`, `property_types`).
- **Selected** per listing type via `BaseScraper.get_filters(listing_type)`.
- **Applied at the source**, not centrally: each scraper folds filter values into
  the portal's own query string / API params, e.g. Immowelt
  `?pma={max_price}&ama={min_area}&rmi={min_rooms}`, Bien'ici
  `{"maxPrice":..., "minRooms":..., "minArea":...}`.

  Consequences:
  - There is **no shared post-scrape filter pass** in `base.py`. Whatever a portal
    returns is saved. `min_price` in particular is generally *not* enforced
    (most filters only push `max`/`min` that the site supports).
  - `property_types` drives a **loop** (one query per type) rather than a filter.

### 5.2 "Scoring" / ranking
- The only value signal is **`price_per_sqm`**, computed in
  `BaseScraper._calc_price_per_sqm()` and stored on the row.
- Ranking by value is implicit: reports/dashboard sort or display by
  `price_per_sqm` / recency. **No weighted score, no preference model.**
- `analysis/deduplication._pick_primary()` has a small "completeness score" but
  that only chooses which duplicate to keep — it is not a listing-quality score.

> For Luxembourg this is a clean extension point: a portal-agnostic
> filter/scoring stage could be added in `base.py` or `analysis/` without touching
> scrapers.

---

## 6. Notification mechanism

- **Single channel: email**, via `src/reports/email_sender.py`.
- Gmail SMTP (`smtp.gmail.com:587`) with `STARTTLS` + `ssl.create_default_context()`.
- Auth via **Gmail App Password**, supplied through env vars
  (`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `RECIPIENT_EMAIL`) and referenced in
  `config.yaml` as `${...}` placeholders resolved by `config.py`.
- Payload: HTML body (the rendered report) + PDF attachment.
- Gated by `reports.email.enabled`; fails soft (logs, returns `False`) on auth/SMTP
  errors so the pipeline still completes.
- The **dashboard** (Streamlit) is a pull channel, not a notification.
- No Telegram/Slack/push/webhook — email is the only push path.

---

## 7. Persistence layer

- **Engine/session:** `src/database/db.py` — module-level singletons
  (`_engine`, `_SessionLocal`) guarded by a `threading.Lock`; `get_session()` is a
  context manager. SQLite opened with `check_same_thread=False`,
  `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`.
- **DB file:** `./data/immo.db` (path from config; gitignored).
- **Schema creation:** `init_db()` → `Base.metadata.create_all()`. Idempotent for
  *new* tables/columns only.
- **Upsert strategy:** `BaseScraper.save_results()` looks up by
  `(source, external_id)`; updates non-null changed fields, refreshes
  `last_seen_at`, re-activates, recomputes `price_per_sqm`, and appends a
  `PriceHistory` row on price change. New rows get an initial `PriceHistory` entry.
- **Inactive sweep:** `main._mark_inactive()` flips `is_active=False` for rows of a
  scraped source not seen since the run's `started_at` (drives "removed this week").

> ⚠️ **No migrations are wired up.** `alembic` is listed as a dependency but there
> is **no `alembic.ini`, no `migrations/` directory, and no Alembic env**.
> Because `country` and `source` are SQLAlchemy `Enum`s (CHECK constraints baked in
> at table-creation time on SQLite), **adding a new country/source value cannot be
> done by `create_all()` against an existing DB** — it needs either a manual
> migration or a schema change. This is the single most important persistence
> constraint for the Luxembourg adaptation (see `ADAPTATION_PLAN.md`).

---

## 8. Runtime / operational shape

- **Entry point:** `python -m src.main` with flags `--scrape-only`,
  `--report-only`, `--dashboard`, `--init-db`, `--config PATH`.
- **Scheduling:** external `cron` (documented in `PIPELINE.md`); `APScheduler` is
  mentioned in `PLAN.md` but not present in code.
- **Concurrency:** scrapers run **sequentially** in an `asyncio` loop, each inside
  one DB session; per-scraper failures are caught and recorded as a failed
  `ScrapeRun` without aborting the others.
- **Config resolution:** `config.yaml` (falls back to `config.example.yaml`) with
  `${ENV}` substitution; missing env vars warn but don't crash.

---

## 9. Test suite (as found)

- `tests/test_scrapers.py` — `PropertyData`, `get_filters`, `get_search_areas`,
  a dummy async scraper. No network.
- `tests/test_analysis.py` — `_is_duplicate` logic + a DB round-trip.
- `tests/test_reports.py` — Jinja2 template rendering + CSS presence.
- Infrastructure-only: the live portal scrapers are **not** covered by tests.
- See `ADAPTATION_PLAN.md` §"Current test status" for the run result on this
  machine (Ubuntu 24.04 / Python 3.11) — **9 pass, 1 fails**, and the failure is a
  genuine code/schema issue, not an environment problem.
