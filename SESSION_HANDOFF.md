# SESSION_HANDOFF.md — current state & how to continue

A quick-orientation doc for picking this project up in a fresh session. For the
full picture see `README.md`; for operations see `RUNBOOK.md`.

## What this is
A personal, **Luxembourg** property monitor (rent / furnished / buy) for a family
relocation (DWS Kirchberg). Scrapes athome.lu, scores listings against
preferences, tracks price history + daily market snapshots, and shows a shortlist
in the terminal or a phone-friendly dashboard. Runs locally, **no paid APIs**.

## Repo facts
- **Active branch:** `claude/nice-clarke-KfULG` (work has been committed/pushed here).
- **Tests:** `pytest -q` → **221 passing, 2 skipped** (legacy `test_analysis.py` /
  `test_reports.py` skip when `thefuzz` / `jinja2` aren't installed — the lean LU
  install omits both). Run before and after any change.
- **Language/stack:** Python 3.11+, SQLite + Alembic, SQLAlchemy 2.0, Pydantic v2,
  httpx + BeautifulSoup, Rich, Streamlit (dashboard only).
- **Everything current lives in** `src/lux_monitor/` + `src/scrapers/luxembourg/` +
  `config/luxembourg.py`. The `src/database`, `src/analysis`, `src/reports`,
  `src/main.py` trees are the **dormant legacy DE/FR tool** — not used by the LU
  monitor (kept, disabled).

## Current behaviour (as of this handoff)
- **Live portal: athome.lu only.** immotop.lu & wortimmo.lu are **parked**
  (Cloudflare-walled + largely duplicate athome) — code kept, disabled via
  `ACTIVE_PORTALS` in `config/luxembourg.py`.
- **A full run harvests ~1,500 matching listings** (buy ≫ rent > furnished) across
  7 communes, in ~5 min.
- **Pipeline** (`run_luxembourg`): scrape → save(+price history) → prune → dedup →
  commute → analyze → score → snapshot. The snapshot stage is **non-fatal** (a
  missing `market_snapshots_lu` table logs a warning; the run still succeeds).
- **Filters:** communes = Luxembourg, Strassen, Bertrange, Mamer, Walferdange,
  Hesperange, Leudelange; rooms 3–8 (pièces — lower bound uses bedrooms+1 estimate,
  upper bound checks the evidenced count so big family homes aren't dropped);
  surface ≥ 80 m²; price caps buy ≤ €3M, rent ≤ €6,000/mo, furnished uncapped.
- **Scoring (0–100, weights sum to 100):** bedrooms 18, drive 20, PT 15,
  foreign% 13, description 12, energy 8, garage 7, garden 7. The property-type /
  amenity fields added 2026-09-13 are **recorded and filterable but not scored** —
  adding one means re-balancing these weights.
- **Property detail (added 2026-09-13):** `property_type` (house/apartment/other) +
  `property_subtype` (detached_house, semi_detached, terraced_house, villa, studio,
  duplex, penthouse…), `floor` (apartments), `land_m2` (houses), `bathrooms_count`,
  terrace/balcony/livingroom m², `is_new_build`, and 11 comfort fields (AC, solar,
  heat pump, pool, attic, basement, wine cellar, heating + type, kitchen type,
  pets). All **tri-state** — `True` / `False` / `None` = *the advert doesn't say*.
  Never render `None` as "no".
- **Decision support:** `Listing.compare_price` is the single buy/rent price basis;
  `finance.py` estimates monthly mortgage (buy) for a like-for-like €/mo vs rent;
  `digest.buy_vs_rent_by_commune` (tested) powers the dashboard's break-even view;
  `snapshots.py` records daily medians for trend charts.

## How athome scraping works (the hard-won part)
- Listings come from the page's embedded `window.__INITIAL_STATE__` JSON, not HTML.
- **Location filter is `q=<hkey>`** (NOT `loc=`, which athome ignores). Per-commune
  hkeys are hardcoded in `COMMUNE_HKEYS` (captured from the site). If a commune
  ever returns the whole-country `total`, its hkey went stale — re-capture from a
  browser search URL.
- **Surface/bedrooms pushed server-side** via `srf_min` / `bedrooms_min` (derived
  from `HARD_FILTERS`) so each search returns only qualifying listings.
- **Furnished** is detected from the listing **text** (meublé/möbliert/furnished);
  athome's furnished URL facet and per-entry `hasFurnished` flag don't work.
- Pagination is full (`paginator.totalPages`, capped by `max_pages_per_source`,
  set to 250 in `config/config.example.yaml`). A per-commune **funnel** log line
  (`athome funnel … total=… pages=… kept=…`) makes a thin harvest diagnosable.
- **Property type / amenities come from the payload; plot size, AC and solar do
  not** — athome's `search.list` has no field for those three, so they're read out
  of the description text (`src/lux_monitor/features.py`, FR/DE/EN, accent-
  insensitive; LU quotes plots in **ares**, 1 are = 100 m²). Two rules hold that
  module together, both pinned by tests after a review found each one violated:
  **"offered" is not "fitted"** (a feature the ad proposes to install reads as
  unknown — `_HYPOTHETICAL_RE`), and **no answer beats a wrong one** (a number the
  parser can't tie to the plot phrase is discarded — `_PLOT_STOP_RE`; bare "land"
  is not a keyword because German *Land* means region). Apartments skip the plot
  text path entirely — a residence's grounds are not the flat's lot.
- The payload is **unversioned**, so `_first(entry, "hasBasementRoom",
  "hasBasement", …)` accepts key-name variants, and `scrape()` ends with an
  `athome field coverage …` log line (`name=count(pct%)` per enriched field). **A
  field at 0% after a full run means the key was renamed**, not that no property
  has one — that line is the only thing standing between a rename and 2,000 rows
  of silent NULL.

## Run it
```bash
cd ~/immo && source .venv/bin/activate
git pull
alembic upgrade head
python -m src.lux_monitor run                 # full pipeline
python -m src.lux_monitor shortlist --top 20  # or --type rent|buy|furnished
python -m src.lux_monitor dashboard           # browser UI (needs: pip install streamlit)
```
Daily automation: `scripts/run_lux.sh` via cron (see RUNBOOK §4). It logs to
`logs/lux_run.log`. Note: cron only fires while the PC is awake.

## Data / persistence — PRECIOUS, see the `protect-database` skill
- One SQLite file: `data/monitor.db` (git-ignored). `LUX_MONITOR_DB_URL` overrides.
- `listings` (+ `price_history_entries`): vanished listings are **deactivated**,
  not deleted — history is preserved.
- `market_snapshots_lu`: one row per (date, type, commune) per run — the trend
  layer. Dashboard "📈 Market trends" charts it; needs ≥2 runs to show a line.
- **🚫 Never `rm data/monitor.db`** — re-scraping can't recover price history,
  trend snapshots or the days-on-market clock. A plain `run` refreshes in place.
- **Backups:** `backup.py` + CLI `backup`/`restore`; `scripts/run_lux.sh` backs up
  to `data/backups/` (git-ignored, keeps 14) **before every run**. There's a
  `.claude/skills/protect-database` skill enforcing the no-wipe rule.
- **Migrations are additive only.** New columns go in with plain
  `op.add_column` (SQLite's native `ALTER TABLE … ADD COLUMN`), never
  `batch_alter_table`, which rebuilds the table by copy-and-swap.
  `tests/test_migration_additive.py` builds a populated DB at the pre-change
  revision, upgrades to head, and asserts every row, value and **rowid** survives —
  so a future destructive migration fails in CI, not on the operator's box.
- **Existing rows enrich in place.** `save_listings` only writes non-`None` values
  (`UPDATABLE_FIELDS` in `src/scrapers/luxembourg/base.py`), so a new column
  backfills on the next run that re-sees the advert. Adding a column means adding
  it to `UPDATABLE_FIELDS` too — `test_enrichment_fields_are_all_updatable` fails
  if you forget, because otherwise it stays NULL forever on every existing row.
- **Price band:** `PRICE_BAND` (config) — per-type (floor, cap); the floor drops
  portal data errors (e.g. a
  €1,111 "sale") so junk never reaches the shortlist.

## Recommended next steps (rough priority)
1. **Gmail daily digest** — the main missing feature. `digest.py` already computes
   new-listings + price-drops; needs an SMTP sender + a `digest` CLI command +
   `.env` (GMAIL_ADDRESS / GMAIL_APP_PASSWORD / RECIPIENT_EMAIL). Mock SMTP in tests.
2. **Let snapshot history accumulate**, then sanity-check the trend charts.
3. **Per-listing detail enrichment** (energy class) — optional; more requests.
4. **Furnished-specialist source** (e.g. HousingAnywhere) — optional coverage.
5. immotop/wortimmo via Playwright — low priority (see "parked" above).

## Conventions / gotchas
- Develop on `claude/nice-clarke-KfULG`; commit + push there.
- Don't commit `data/*.db`, `.env`, `__pycache__` (already in `.gitignore`).
- Tests set/leak `LUX_MONITOR_DB_URL` if you run ad-hoc scripts in the same shell —
  `unset` it before `pytest` to avoid spurious failures.
- The sandbox/CI has **no network to athome** (allowlist); live scraping is
  workstation-only. Parsing is covered by fixtures in `tests/fixtures/luxembourg/`.
