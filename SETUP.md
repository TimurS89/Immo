# Setup — Luxembourg Property Monitor (Ubuntu 24.04 / Python 3.11+)

> **Just want to run the Luxembourg monitor?** Use the **lean install** in
> [`README.md` → Quick start](README.md#quick-start) — it needs only
> `httpx`/`bs4`/`sqlalchemy`/… (no Playwright, no WeasyPrint) and is what the
> live system actually uses. The browser dashboard additionally needs
> `pip install streamlit`. The sections below describe the **full** install
> (including the dormant DE/FR stack and its heavy deps) and are optional.

Workstation setup for the autonomous monitor. Ubuntu 24.04's system Python is
PEP-668 "externally managed", so **always use the project venv** — never
`pip install` into system Python.

## 1. System packages (WeasyPrint native libs + Playwright deps)

```bash
sudo apt-get update
# WeasyPrint needs Pango / Cairo / gdk-pixbuf / ffi at runtime:
sudo apt-get install -y \
  libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
  libgdk-pixbuf-2.0-0 libffi-dev libharfbuzz0b shared-mime-info
```

## 2. Python venv + dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"        # runtime + pytest/pytest-asyncio
```

Pin notes (already set in `pyproject.toml` / `requirements.txt`):
- `playwright>=1.49` — reliable `install --with-deps` on Ubuntu 24.04 (noble).
- `playwright-stealth>=1.0.6,<2.0` — code uses the 1.x `stealth_async` API
  (2.x is a breaking rename).
- `weasyprint>=62` — needs the apt libs above.
- **`kaleido` removed** — static image export is unused; Plotly charts are
  emitted as interactive HTML.

Verified resolved versions: playwright 1.60, playwright-stealth 1.0.6,
weasyprint 68.1, plotly 5.24, streamlit 1.58.

## 3. Playwright browser

```bash
playwright install --with-deps chromium
```

> Note: this download is **blocked in the cloud sandbox** (egress policy), so it
> must be run on the workstation. Only needed to actually run scrapers (Phase 3+);
> the test suite does not require a browser.

## 4. Secrets (`.env`)

```bash
cp .env.example .env        # optional — the monitor runs with no .env at all
# No API keys are required: commute is an offline estimate and description
# analysis is heuristic (no Google Maps key, no LLM key).
# Optionally set LUX_MONITOR_DB_URL to relocate the database.
# Email alerts are not implemented yet (GMAIL_* are placeholders for later).
```

## 5. Database (Alembic-managed, `data/monitor.db`)

The project is migration-based (no more `create_all` for the canonical schema):

```bash
alembic upgrade head        # builds data/monitor.db (LU + legacy DE/FR tables)
```

Override the DB location with `LUX_MONITOR_DB_URL` if needed.

> ⚠️ **`data/monitor.db` is precious** — it accumulates price history, daily
> market snapshots, and the days-on-market clock that re-scraping **cannot**
> rebuild. **Never `rm` it.** Back up / restore with
> `python -m src.lux_monitor backup` / `restore` (the cron wrapper backs up before
> every run). See `CHEATSHEET.md §7` and the `protect-database` skill.

## 6. Run the tests

```bash
pytest -q                   # expect: all green
```

## 7. Luxembourg scrapers (live run — workstation only)

> **Going live?** Follow **`RUNBOOK.md`** — it walks through validating the
> scraper selectors against real HTML, a supervised first run, tuning, and cron
> scheduling, in order. This section is the quick reference.

The LU scrapers (`src/scrapers/luxembourg/`) target athome.lu, immotop.lu and
wortimmo.lu and write to `lux_monitor`. Their **parsing logic is unit-tested
against synthetic fixtures** in `tests/fixtures/luxembourg/`; the SERP selectors
in those fixtures are *representative placeholders* and **must be validated
against live HTML** before the first real run (capture a few real SERP pages and
adjust the selectors / re-save fixtures).

Only **scraping** needs network egress (blocked in the cloud sandbox). The LU
scrapers use plain `httpx` + BeautifulSoup, so the Playwright browser is **not**
required for them — you can skip `playwright install` if you only run the LU
monitor.

### Run it

```bash
alembic upgrade head                          # once: build the schema
python -m src.lux_monitor run --max-pages 1   # first: a small, polite test run
python -m src.lux_monitor run                 # full pipeline + shortlist
python -m src.lux_monitor shortlist --top 20  # view the shortlist anytime
python -m src.lux_monitor run --no-scrape     # recompute scores offline (no network)
```

`run` does: scrape → save → dedup → commute (offline estimate) → analyze
(heuristics) → score, then prints the ranked shortlist. `--no-scrape` re-runs
only the offline stages — handy after tuning. If you skip Alembic, `run
--init-db` will `create_all` the schema as a dev fallback.

### Schedule it (cron)

`scripts/run_lux.sh` activates the venv, runs the pipeline, and logs to
`logs/lux_run.log`. Add it to your crontab (`crontab -e`):

```cron
# every day at 07:15
15 7 * * *  /ABSOLUTE/PATH/TO/Immo/scripts/run_lux.sh
```

### Tuning knobs

All non-negotiables live in `config/luxembourg.py`:
- `TARGET_COMMUNES` — the commune set (+ foreign %, school coords).
- `HARD_FILTERS_RENT` / `HARD_FILTERS_BUY` — bedrooms, surface, price/rent band, commute caps.
- `SCORING_WEIGHTS` — soft-score weights (must sum to 100).

Estimator/heuristic constants live in `src/lux_monitor/commute.py` (speeds, rush
factor) and `src/lux_monitor/analysis.py` (keyword rules, penalties/bonuses).
After changing any of these, re-rank existing data with `run --no-scrape`.

