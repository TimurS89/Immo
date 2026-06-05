# Immo Property Search Automation — Pipeline Guide

> ⚠️ **LEGACY / HISTORICAL.** Describes the original Germany/France tool. For the
> current **Luxembourg** monitor use `README.md` (overview) and `RUNBOOK.md`
> (operations). Kept for reference only.

## Quick Start (Step by Step)

### 1. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
# .venv\Scripts\activate       # Windows

pip install -r requirements.txt
```

### 2. Install Playwright browsers

```bash
playwright install chromium
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx    # Gmail > Settings > App Passwords
RECIPIENT_EMAIL=you@gmail.com
```

### 4. Configure search parameters

```bash
cp config/config.example.yaml config/config.yaml
```

Edit `config/config.yaml` to customize:
- **Search areas** — cities, postal codes, departments
- **Filters** — price range, min rooms, min area, property types
- **Enabled scrapers** — pick which sites to scrape
- **Email** — enable/disable report delivery

### 5. Initialize the database

```bash
python -m src.main --init-db
```

Creates `data/immo.db` (SQLite with WAL mode).

### 6. Run the full pipeline

```bash
python -m src.main
```

### 7. Run individual modes

```bash
# Scrape only (no report)
python -m src.main --scrape-only

# Report only (from existing data)
python -m src.main --report-only

# Launch interactive dashboard
python -m src.main --dashboard

# Use a custom config file
python -m src.main --config /path/to/config.yaml
```

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    src/main.py                          │
│                  (Orchestrator)                          │
│                                                         │
│  1. Load config ──► 2. Init DB ──► 3. Scrape            │
│                                       │                 │
│  6. Email report ◄── 5. Generate  ◄── 4. Analyze        │
│      (optional)       report          (dedup +           │
│                                        market stats)    │
└─────────────────────────────────────────────────────────┘
```

### Stage 1 — Configuration (`src/config.py`)

- Loads `config/config.yaml` + `.env` file
- Resolves `${ENV_VAR}` placeholders in YAML values
- Validates all config via Pydantic models
- Key classes: `AppConfig`, `SearchArea`, `Filters`, `ScrapersConfig`

### Stage 2 — Database Init (`src/database/`)

- **`db.py`** — SQLAlchemy engine + session context manager (thread-safe singleton)
- **`models.py`** — 4 tables:
  - `properties` — main listing data (price, area, rooms, location, source)
  - `price_history` — tracks price changes over time per property
  - `scrape_runs` — logs each scraper execution (counts, status, timing)
  - `market_snapshots` — periodic aggregate stats for trend analysis

### Stage 3 — Scraping (`src/scrapers/`)

9 scrapers across 2 countries:

| Country | Scraper | Module | Method |
|---------|---------|--------|--------|
| DE | ImmoScout24 | `germany/immoscout24.py` | Playwright (browser) |
| DE | Immowelt | `germany/immowelt.py` | Playwright (browser) |
| DE | Kleinanzeigen | `germany/kleinanzeigen.py` | Playwright (browser) |
| DE | Wohnungsbörse | `germany/wohnungsboerse.py` | httpx (API) |
| FR | LeBonCoin | `france/leboncoin.py` | Playwright (browser) |
| FR | SeLoger | `france/seloger.py` | Playwright (browser) |
| FR | Bien'ici | `france/bienici.py` | httpx (API) |
| FR | PAP | `france/pap.py` | httpx (API) |
| FR | ParuVendu | `france/paruvendu.py` | httpx (API) |

Key components:
- **`base.py`** — `BaseScraper` ABC with shared logic: random delays, filter helpers, `save_results()` (upsert with price change tracking)
- **`browser.py`** — Playwright wrapper with stealth plugin + optional proxy rotation
- **`captcha.py`** — Optional 2Captcha integration

Data flow: Scraper → `list[PropertyData]` → `save_results()` → DB upsert + `PriceHistory` + `ScrapeRun`

### Stage 4 — Analysis (`src/analysis/`)

- **`deduplication.py`** — Cross-source duplicate detection using fuzzy matching on address, title, price (±5%), area (±10%), and exact room count
- **`market_stats.py`** — Computes avg/median prices per m², listing counts, and stores `MarketSnapshot` records for trend charts
- **`price_tracker.py`** — Detects price drops/increases from `PriceHistory` table

### Stage 5 — Report Generation (`src/reports/`)

- **`generator.py`** — Orchestrates data collection, chart creation, HTML/PDF rendering, and email dispatch
- **`charts.py`** — Plotly charts: price distributions, price-per-m² trends, source breakdowns
- **`pdf.py`** — WeasyPrint HTML-to-PDF conversion
- **`templates/`** — Jinja2 HTML templates + CSS for email body and PDF layout

### Stage 6 — Email Delivery (`src/reports/email_sender.py`)

- Gmail SMTP with TLS certificate verification
- Sends HTML body + PDF attachment
- Configurable via `reports.email` in config

### Interactive Dashboard (`src/dashboard/app.py`)

- Streamlit app on `http://127.0.0.1:8501`
- Filters: country, listing type, property type, price range, area
- Shows: metrics, listings table (200 max), median price/m² trend chart

---

## File Tree (Key Files)

```
src/
├── main.py                          # CLI entry point + pipeline orchestrator
├── config.py                        # YAML + env var config loader
├── database/
│   ├── db.py                        # Engine + session management
│   └── models.py                    # SQLAlchemy table definitions
├── scrapers/
│   ├── base.py                      # BaseScraper ABC + save logic
│   ├── browser.py                   # Playwright stealth browser
│   ├── captcha.py                   # 2Captcha solver (optional)
│   ├── germany/                     # 4 DE scrapers
│   └── france/                      # 5 FR scrapers
├── analysis/
│   ├── deduplication.py             # Cross-source fuzzy dedup
│   ├── market_stats.py              # Aggregate market snapshots
│   └── price_tracker.py             # Price change detection
├── reports/
│   ├── generator.py                 # Report orchestrator
│   ├── charts.py                    # Plotly chart builders
│   ├── pdf.py                       # WeasyPrint PDF export
│   ├── email_sender.py              # Gmail SMTP sender
│   └── templates/                   # Jinja2 HTML + CSS
└── dashboard/
    └── app.py                       # Streamlit interactive UI
config/
├── config.example.yaml              # Template config
data/                                # SQLite DB + reports output
logs/                                # Log files
tests/                               # pytest test suite
```

---

## Scheduling (Cron)

To run daily at 6 AM:

```bash
# crontab -e
0 6 * * * cd /path/to/Immo && .venv/bin/python -m src.main >> logs/cron.log 2>&1
```

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
