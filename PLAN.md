# Property Search Automation - Implementation Plan

> ⚠️ **LEGACY / HISTORICAL.** Describes the original Germany/France tool. The
> project was retargeted to **Luxembourg** — see `README.md` and
> `SESSION_HANDOFF.md` for the current system. Kept for reference only.

## Overview
Automated Python tool for personal property search in **Baden-Baden, Germany** and **Alsace (Bas-Rhin 67 + Haut-Rhin 68), France**. Runs weekly, produces comprehensive reports (HTML email + PDF + local web dashboard), tracks price trends over time.

## User Requirements Summary
- **Property types**: Apartments, Houses, Land/Plots (no commercial)
- **Buy budget**: 0 - 1,000,000 EUR
- **Rent budget**: up to 2,500 EUR/month
- **Min size**: 50 sqm
- **Min rooms**: 3+
- **Germany area**: Baden-Baden city
- **France area**: All of Alsace (departments 67 + 68)
- **Report delivery**: HTML email (Gmail) + PDF file + local web dashboard
- **Runs on**: Local PC (Linux), weekly via cron
- **Language**: English output

---

## Target Websites (Priority Order)

### Germany (Baden-Baden)
| Priority | Platform | URL | Difficulty | Strategy |
|----------|----------|-----|------------|----------|
| 1 | **ImmobilienScout24** | immobilienscout24.de | Hard | Playwright + stealth + captcha fallback |
| 2 | **Immowelt** (includes Immonet) | immowelt.de | Medium | Playwright, Next.js `__NEXT_DATA__` JSON extraction |
| 3 | **Kleinanzeigen** | kleinanzeigen.de | Hard | Playwright + stealth + residential proxy |
| 4 | **Wohnungsboerse.net** | wohnungsboerse.net | Easy | httpx + BeautifulSoup |

### France (Alsace)
| Priority | Platform | URL | Difficulty | Strategy |
|----------|----------|-----|------------|----------|
| 1 | **LeBonCoin** | leboncoin.fr | Hard | Playwright + stealth (DataDome protection) |
| 2 | **SeLoger** | seloger.com | Hard | Playwright + stealth (DataDome) |
| 3 | **Bien'ici** | bienici.com | Medium | Reverse-engineered JSON API |
| 4 | **PAP** | pap.fr | Easy-Medium | httpx + BeautifulSoup |
| 5 | **ParuVendu** | paruvendu.fr | Easy | httpx + BeautifulSoup |

---

## Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Language** | Python 3.11+ | Best ecosystem for scraping |
| **Scraping (JS sites)** | Playwright + playwright-stealth | Best for modern JS-heavy sites, async, auto-wait |
| **Scraping (simple sites)** | httpx + BeautifulSoup4 | Lightweight for simpler targets |
| **Anti-detection** | playwright-stealth + rotating user agents + delays | Avoid triggering CAPTCHAs |
| **CAPTCHA solving** | 2Captcha API (fallback only) | Cheap (~$3/1000), reliable |
| **Proxies** | Optional residential proxy support | Configurable, not mandatory for weekly low-volume |
| **Database** | SQLite | Zero-config, perfect for personal use, portable |
| **ORM** | SQLAlchemy 2.0 | Clean schema management + migrations via Alembic |
| **Charts** | Plotly | Interactive HTML charts, static PNG for PDF |
| **Report HTML** | Jinja2 templates | Professional HTML reports |
| **Report PDF** | WeasyPrint | HTML-to-PDF conversion |
| **Dashboard** | Streamlit | Minimal code for interactive local dashboard |
| **Email** | smtplib + Gmail App Password | Simple, reliable |
| **Scheduling** | System cron + APScheduler (for dashboard) | Simple weekly runs |
| **Config** | YAML (pyyaml) | Human-readable configuration |
| **Logging** | Python logging + Rich | Structured logs with color output |

---

## Project Structure

```
Immo/
├── config/
│   ├── config.yaml              # Main configuration (search params, credentials)
│   ├── config.example.yaml      # Template without secrets
│   └── proxies.txt              # Optional proxy list
├── src/
│   ├── __init__.py
│   ├── main.py                  # Entry point / orchestrator
│   ├── config.py                # Configuration loader
│   ├── database/
│   │   ├── __init__.py
│   │   ├── models.py            # SQLAlchemy models (Property, PriceHistory, ScrapeRun)
│   │   ├── db.py                # Database connection & session management
│   │   └── migrations/          # Alembic migrations
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── base.py              # Abstract base scraper class
│   │   ├── browser.py           # Shared Playwright browser management + stealth
│   │   ├── captcha.py           # 2Captcha integration
│   │   ├── germany/
│   │   │   ├── __init__.py
│   │   │   ├── immoscout24.py   # ImmobilienScout24 scraper
│   │   │   ├── immowelt.py      # Immowelt scraper
│   │   │   ├── kleinanzeigen.py # Kleinanzeigen scraper
│   │   │   └── wohnungsboerse.py# Wohnungsboerse.net scraper
│   │   └── france/
│   │       ├── __init__.py
│   │       ├── leboncoin.py     # LeBonCoin scraper
│   │       ├── seloger.py       # SeLoger scraper
│   │       ├── bienici.py       # Bien'ici scraper
│   │       ├── pap.py           # PAP scraper
│   │       └── paruvendu.py     # ParuVendu scraper
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── deduplication.py     # Cross-platform duplicate detection
│   │   ├── price_tracker.py     # Price change detection & trends
│   │   └── market_stats.py      # Market statistics & aggregations
│   ├── reports/
│   │   ├── __init__.py
│   │   ├── generator.py         # Report orchestrator
│   │   ├── charts.py            # Plotly chart generation
│   │   ├── pdf.py               # WeasyPrint PDF generation
│   │   ├── email_sender.py      # Gmail SMTP sender
│   │   └── templates/
│   │       ├── report.html      # Jinja2 HTML email template
│   │       ├── pdf_report.html  # Jinja2 PDF template
│   │       └── styles.css       # Report styling
│   └── dashboard/
│       ├── __init__.py
│       └── app.py               # Streamlit dashboard
├── data/
│   ├── immo.db                  # SQLite database (gitignored)
│   └── reports/                 # Generated PDF reports (gitignored)
├── logs/                        # Log files (gitignored)
├── tests/
│   ├── __init__.py
│   ├── test_scrapers.py
│   ├── test_analysis.py
│   └── test_reports.py
├── requirements.txt
├── pyproject.toml
├── .gitignore
├── .env.example                 # Template for secrets
└── README.md
```

---

## Database Schema

### Properties Table
```
- id (PK, UUID)
- external_id (unique per source)
- source (enum: immoscout24, immowelt, kleinanzeigen, leboncoin, seloger, bienici, pap, paruvendu, wohnungsboerse)
- country (DE / FR)
- listing_type (rent / buy)
- property_type (apartment / house / land)
- title
- description
- price (EUR)
- price_per_sqm (calculated)
- rooms (float, e.g. 3.5)
- living_area_sqm
- plot_area_sqm (nullable)
- address_city
- address_postal_code
- address_street (nullable)
- latitude (nullable)
- longitude (nullable)
- energy_rating (nullable)
- year_built (nullable)
- floor (nullable)
- has_balcony (bool)
- has_garden (bool)
- has_garage (bool)
- has_elevator (bool)
- image_urls (JSON array)
- listing_url
- contact_info (nullable)
- first_seen_at (timestamp)
- last_seen_at (timestamp)
- is_active (bool)
- raw_data (JSON, full scraped data for debugging)
- created_at
- updated_at
```

### PriceHistory Table
```
- id (PK)
- property_id (FK -> Properties)
- price (EUR)
- recorded_at (timestamp)
```

### ScrapeRuns Table
```
- id (PK)
- source
- started_at
- completed_at
- listings_found (int)
- new_listings (int)
- updated_listings (int)
- errors (int)
- status (success / partial / failed)
- log (text)
```

### MarketSnapshots Table
```
- id (PK)
- snapshot_date
- country
- listing_type
- property_type
- avg_price
- median_price
- avg_price_per_sqm
- median_price_per_sqm
- total_listings
- new_listings_this_week
- removed_listings_this_week
```

---

## Implementation Phases

### Phase 1: Foundation (Core infrastructure)
1. Project setup (pyproject.toml, requirements, .gitignore)
2. Configuration system (YAML config + .env for secrets)
3. Database models + migrations
4. Base scraper class with Playwright browser management
5. Stealth/anti-detection layer
6. Logging infrastructure

### Phase 2: German Scrapers
1. ImmobilienScout24 scraper (hardest, do first)
2. Immowelt scraper
3. Kleinanzeigen scraper
4. Wohnungsboerse.net scraper
5. CAPTCHA solving integration (2Captcha)

### Phase 3: French Scrapers
1. LeBonCoin scraper
2. SeLoger scraper
3. Bien'ici scraper (JSON API approach)
4. PAP scraper
5. ParuVendu scraper

### Phase 4: Analysis Engine
1. Cross-platform deduplication (fuzzy matching on address + price + size)
2. Price change tracking
3. Market statistics calculation
4. Weekly trend analysis

### Phase 5: Report Generation
1. Plotly charts (price trends, distributions, new vs removed)
2. Jinja2 HTML report template
3. WeasyPrint PDF generation
4. Gmail email sender
5. Report orchestrator (combines all)

### Phase 6: Dashboard & Scheduling
1. Streamlit dashboard (browse listings, view trends, filter/search)
2. Cron job setup
3. Main orchestrator script (scrape -> analyze -> report -> email)

---

## Weekly Report Contents

### 1. Executive Summary
- Total active listings (by country, by type)
- New listings this week
- Removed/expired listings this week
- Notable price changes

### 2. New Listings (detailed)
- Table with: thumbnail, title, price, size, rooms, location, link
- Sorted by: date added, then by value (price/sqm)
- Separate sections for: Buy (Germany), Buy (France), Rent (Germany), Rent (France)

### 3. Price Reductions
- Properties with price drops this week
- Amount and percentage of reduction

### 4. Market Overview
- Average/median price per sqm (buy) - trend chart over time
- Average/median rent per sqm - trend chart over time
- Number of listings over time (supply trend)
- Price distribution histogram
- Breakdown by property type and location

### 5. Properties Removed (no longer listed)
- May indicate sold/rented - useful market signal

---

## Key Technical Decisions

1. **Playwright over Selenium**: Modern async API, better stealth plugin support, auto-waiting reduces flakiness
2. **SQLite over PostgreSQL**: Zero-config, single-file database, perfect for personal use. Can migrate later if needed.
3. **Stealth-first, CAPTCHA-second**: Most sites won't trigger CAPTCHAs with proper stealth + low request volume (weekly)
4. **Deduplication via fuzzy matching**: Same property listed on multiple sites → deduplicated by address similarity + price proximity + area match
5. **Incremental scraping**: Only fetch new/changed listings each week, not full re-scrape. Compare against existing database.
6. **Resilient per-scraper execution**: If one scraper fails, others still run. Failures logged and reported.

---

## Configuration (config.yaml)

```yaml
search:
  germany:
    - name: "Baden-Baden"
      city: "Baden-Baden"
      postal_codes: ["76530", "76532", "76534"]
      radius_km: 0  # city only
  france:
    - name: "Alsace"
      departments: ["67", "68"]  # Bas-Rhin, Haut-Rhin

filters:
  buy:
    min_price: 0
    max_price: 1000000
    min_rooms: 3
    min_area_sqm: 50
    property_types: ["apartment", "house", "land"]
  rent:
    min_price: 0
    max_price: 2500
    min_rooms: 3
    min_area_sqm: 50
    property_types: ["apartment", "house"]

scrapers:
  enabled:
    germany: ["immoscout24", "immowelt", "kleinanzeigen", "wohnungsboerse"]
    france: ["leboncoin", "seloger", "bienici", "pap", "paruvendu"]
  request_delay_seconds: [3, 8]  # random range
  max_pages_per_source: 20
  headless: true
  captcha_service: "2captcha"  # or null to disable

reports:
  output_dir: "./data/reports"
  email:
    enabled: true
    smtp_server: "smtp.gmail.com"
    smtp_port: 587
    sender_email: "${GMAIL_ADDRESS}"
    sender_password: "${GMAIL_APP_PASSWORD}"
    recipient_email: "${RECIPIENT_EMAIL}"

proxy:
  enabled: false
  type: "residential"  # residential / datacenter
  file: "./config/proxies.txt"  # one per line: protocol://user:pass@host:port

dashboard:
  port: 8501
  host: "127.0.0.1"

database:
  path: "./data/immo.db"

logging:
  level: "INFO"
  file: "./logs/immo.log"
```
