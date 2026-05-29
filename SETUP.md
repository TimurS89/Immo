# Setup — Luxembourg Property Monitor (Ubuntu 24.04 / Python 3.11)

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
cp .env.example .env
# Fill in GMAIL_ADDRESS / GMAIL_APP_PASSWORD / RECIPIENT_EMAIL
# (Phase 4+ will add GOOGLE_MAPS_API_KEY; Phase 6 Telegram token; etc.)
```

## 5. Database (Alembic-managed, `data/monitor.db`)

The project is migration-based (no more `create_all` for the canonical schema):

```bash
alembic upgrade head        # builds data/monitor.db (LU + legacy DE/FR tables)
```

Override the DB location with `LUX_MONITOR_DB_URL` if needed.

## 6. Run the tests

```bash
pytest -q                   # expect: all green
```

## 7. Luxembourg scrapers (live run — workstation only)

The LU scrapers (`src/scrapers/luxembourg/`) target athome.lu, immotop.lu and
wortimmo.lu and write to `lux_monitor`. Their **parsing logic is unit-tested
against synthetic fixtures** in `tests/fixtures/luxembourg/`; the SERP selectors
in those fixtures are *representative placeholders* and **must be validated
against live HTML** before the first real run (capture a few real SERP pages and
adjust the selectors / re-save fixtures).

Live scraping needs the Playwright browser + network egress (blocked in the cloud
sandbox), so run it on the workstation:

```python
import asyncio
from src.config import load_config
from src.lux_monitor.db import get_engine, make_session_factory, init_db
from src.scrapers.luxembourg import run_luxembourg

cfg = load_config()
engine = init_db(get_engine())
session = make_session_factory(engine)()
print(asyncio.run(run_luxembourg(cfg, session)))  # scrape -> save -> dedup
```

