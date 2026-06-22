# Luxembourg Property Monitor

A personal, automated assistant for finding a family home to **rent (or buy) in
Luxembourg**. It scrapes Luxembourg real-estate portals on a schedule, keeps a
local history of every listing, throws away everything that doesn't meet a set of
hard requirements, **scores** what's left against your preferences (commute,
walkability, expat-friendliness, condition…), and shows you a ranked shortlist.

It runs entirely on your own machine, needs **no paid API keys**, and is meant to
replace the daily chore of refreshing property sites by hand.

> **Context this was built for:** relocating to Luxembourg for a job at **DWS
> (Kirchberg)** with a family — so the whole tool is opinionated around *a 4‑bedroom
> home, a short rush‑hour commute to Kirchberg, good schools nearby, and
> expat‑friendly communes*. All of that is configurable (see
> [Configuration](#configuration)).

---

## Table of contents
- [What it does](#what-it-does)
- [Current status](#current-status) — what works, what doesn't
- [How it works](#how-it-works) (the pipeline)
- [Quick start](#quick-start)
- [Using it day to day](#using-it-day-to-day)
- [Configuration](#configuration) — the knobs
- [Project layout](#project-layout)
- [Design decisions](#design-decisions)
- [What's outstanding](#whats-outstanding)
- [Caveats](#caveats)

---

## What it does

Every run, the tool:

1. **Scrapes** the configured Luxembourg portals for three categories — **furnished
   rentals, long‑term rentals (flats + houses), and properties to buy** — in your
   target communes, filtered server‑side by surface and bedrooms. (It stores the
   advert link, not photos.)
2. **Stores** each listing in a local SQLite database, tracking price changes,
   when it first/last appeared, and when it disappears (a useful "rented/sold"
   signal).
3. **Prunes** anything already stored that no longer matches the current filters
   (so tightening a filter trims the database on the next run, no re‑scrape).
4. **De‑duplicates** the same property listed on more than one portal.
5. **Estimates the commute** (drive + public transport, at rush hour) to the office.
6. **Analyzes the description** (FR/DE/EN) for red flags ("to renovate", "viager",
   noisy road…) and highlights ("renovated", "bright", "near transport").
7. **Filters** out anything failing your hard criteria, then **scores** the rest
   0–100 on a weighted blend of your preferences.
8. **Records a market snapshot** — daily median price, €/m² and counts per type
   and commune — building a trend you can chart over months.
9. **Surfaces** a ranked shortlist in the terminal and a browser dashboard.

> Note: the project was originally a Germany/France property tool and was
> **retargeted to Luxembourg**. The old DE/FR code still exists in the repo but is
> dormant — everything current lives under `src/lux_monitor/` and
> `src/scrapers/luxembourg/`.

---

## Current status

The pipeline is built, **tested (158 passing tests)**, and **running live against
athome.lu** end‑to‑end. A full run currently harvests ~1,500 matching listings
across the three types in the 7 target communes.

| Component | Status |
|---|---|
| Database + schema (SQLite, Alembic migrations) | ✅ Done |
| Config: communes, hard filters, price caps, scoring weights | ✅ Done |
| **athome.lu** scraper | ✅ **Working live** — embedded JSON, server‑side location/surface/bedroom filters, full pagination |
| immotop.lu scraper | 🅿️ **Parked** — Cloudflare‑walled *and* largely duplicates athome; code kept, disabled in `ACTIVE_PORTALS` |
| wortimmo.lu scraper | 🅿️ **Parked** — same (also a bot‑challenge) |
| Retroactive prune (re‑apply filters to stored data) | ✅ Done |
| Cross‑portal de‑duplication | ✅ Done |
| Commute estimate (offline drive + PT) | ✅ Done |
| Description analysis (offline FR/DE/EN heuristics) | ✅ Done |
| Hard filter + price caps + weighted scoring | ✅ Done |
| Market snapshots + dashboard trend charts | ✅ Done |
| Buy‑vs‑rent mortgage comparison + break‑even | ✅ Done |
| Orchestrator CLI (`python -m src.lux_monitor`) | ✅ Done |
| Browser dashboard (Streamlit, phone‑friendly) | ✅ Done |
| Cron wrapper for daily scheduling | ✅ Done |
| DB backup / restore (auto before each run) | ✅ Done |
| Push notifications (email / Telegram) | ❌ Not built |

**In practice:** a real run scrapes **athome.lu** (the dominant LU portal), stores
and scores listings, records a market snapshot, and surfaces a ranked shortlist in
the terminal or the dashboard. The other two portals actively block automated
requests and are parked (see [Outstanding](#whats-outstanding)).

---

## How it works

```
        ┌──────────────────────────────── run_luxembourg() ────────────────────────────────┐
portals→│ scrape → save(+price history) → prune → dedup → commute → analyze → score → snapshot │→ shortlist
(athome)└──────────────────────────────────────────────────────────────────────────────────┘     + dashboard
              │           │                          │          │         │          │
          httpx +      SQLite                    haversine   FR/DE/EN   weighted    daily
          JSON parse   (upsert)                  estimate    keywords   0–100       medians
```

- **Resilient by design:** if one portal fails (DNS, 403, a site redesign), it's
  logged and skipped — the other portals and all the offline stages still run.
- **Incremental:** re‑running never clobbers enrichment (commute/score) or your
  own notes; it updates facts, records price changes, and marks vanished listings
  inactive.
- **Offline‑first:** the commute and description analysis are computed locally, so
  the only thing that needs the network is the scrape itself.

### The two-stage selection

1. **Hard filter** — commune in the target set, **3–8 rooms** (pièces; when the
   portal doesn't report them we estimate bedrooms + 1, so "3 rooms" ≈ 2 bedrooms),
   **surface ≥ 80 m²**, and a **per‑type price cap** (buy ≤ €3M, rent ≤ €6,000/mo,
   furnished uncapped). Commute is **not** a knockout — it's a soft indicator.
2. **Soft score** (0–100) — a weighted blend of: bedrooms (18), drive time (20),
   PT time (15), foreign‑resident % of the commune (13), description quality (12),
   energy class (8), garage (7), garden (7). Missing data scores *neutral*, never
   punishing.

---

## Quick start

Requires **Python 3.11+** (tested on 3.12) on Linux/macOS.

```bash
git clone https://github.com/timurs89/immo.git
cd immo

python3 -m venv .venv && source .venv/bin/activate

# Lean install — just what the Luxembourg monitor needs (no Playwright/WeasyPrint):
pip install -e . --no-deps
pip install "sqlalchemy>=2.0,<3.0" "pydantic>=2.5,<3.0" "rich>=13.7,<14.0" \
            "httpx>=0.25" "beautifulsoup4>=4.12" "pyyaml>=6.0" \
            "python-dotenv>=1.0" "alembic>=1.13" "pytest>=8.0" "pytest-asyncio>=0.23"

alembic upgrade head          # build the SQLite database (data/monitor.db)

python -m src.lux_monitor run                 # full harvest (~5 min, polite delays)
python -m src.lux_monitor shortlist --top 20  # see the ranked results
```

No `.env` or API keys are required. (For the full install with the dormant DE/FR
stack, see `SETUP.md`.)

---

## Using it day to day

> **Operator cheatsheet:** `CHEATSHEET.md` has copy‑paste snippets for the daily
> check, dashboard, manual runs, tuning, backups and troubleshooting — use that to
> run the tool without re‑deriving commands.

```bash
# Full pipeline: scrape every portal, then print the shortlist
python -m src.lux_monitor run

# Just look at the current shortlist (no scraping)
python -m src.lux_monitor shortlist --top 20
python -m src.lux_monitor shortlist --type rent      # or --type buy

# Re-score existing listings WITHOUT re-scraping (after changing the config)
python -m src.lux_monitor run --no-scrape

# Explain why one listing got the score it did
python -m src.lux_monitor.scoring --explain <listing_id>

# Browser dashboard (filter/sort, price drops, market trends, buy-vs-rent) — phone-friendly
python -m src.lux_monitor dashboard
#   PC:    http://localhost:8501
#   phone: http://<this-machine-LAN-IP>:8501   (same Wi-Fi)

# Back up / restore the database (the cron job also backs up before every run)
python -m src.lux_monitor backup
python -m src.lux_monitor restore        # newest backup; asks to confirm
```

> ⚠️ **Never `rm data/monitor.db`.** It permanently loses the price history,
> trend snapshots and days-on-market clock that re-scraping can't rebuild. A plain
> `run` refreshes in place. See `CHEATSHEET.md §7`.

**Reading the output:** the run prints a one‑line summary like
`pipeline: new=… pruned=… scored=… snapshot_segments=…`, and a per‑commune
**funnel** line (`athome funnel rent Luxembourg total=… pages=… kept=…`) so a thin
harvest is instantly diagnosable. The shortlist table shows score, type, commune,
bedrooms, m², price, drive/PT minutes, energy class, garage/garden.

> The dashboard needs Streamlit (not in the lean install): `pip install streamlit`.

**Scheduling (cron):** `scripts/run_lux.sh` activates the venv, runs the pipeline,
and logs to `logs/lux_run.log`. Add it with `crontab -e`:

```cron
# every day at 07:15
15 7 * * *  /home/you/immo/scripts/run_lux.sh
```

See **`RUNBOOK.md`** for the full go‑live walkthrough (including how to re‑validate
a scraper if a site changes its markup).

---

## Configuration

The non‑negotiables live in one file — **`config/luxembourg.py`** — as plain Python
constants (no YAML to wrangle):

- **`TARGET_COMMUNES`** — the 7 communes searched/scored: Luxembourg, Strassen,
  Bertrange, Mamer, Walferdange, Hesperange, Leudelange — each with its
  foreign‑resident %, train flag, and centre coordinates (for the commute estimate).
- **`HARD_FILTERS`** — the knockouts: **3–8 rooms, surface ≥ 80 m², commune**.
- **`MAX_PRICE_EUR`** — per‑type price ceilings (buy €3M, rent €6,000, furnished `None`).
- **`MIN_PRICE_EUR`** — per‑type price floors that drop portal data errors (a
  €1,111 "sale", a €5/mo "rent"): buy €150k, rent €1,000, furnished €800.
- **`SCORING_WEIGHTS`** — the soft‑score weights (must sum to 100): bedrooms,
  commute (drive + PT), foreign %, description quality, energy, garage, garden.
- **`COMMUNE_HKEYS`** — athome's location‑filter token per commune (captured from
  the site; the scraper sends these so each search is server‑side filtered).
- **`DWS_OFFICE`** — the commute destination (Kirchberg).

Tuning anything here, then `python -m src.lux_monitor run --no-scrape`, re‑ranks
your existing data instantly. Estimator constants (assumed speeds, rush‑hour
factor) are in `src/lux_monitor/commute.py`; the description keyword rules are in
`src/lux_monitor/analysis.py`.

---

## Project layout

```
config/luxembourg.py          # communes, hard filters, scoring weights, office (the "spec")
src/lux_monitor/
  models.py                   # SQLAlchemy schema (listings, price history)
  schemas.py                  # Pydantic validation (ListingCreate)
  db.py                       # engine/session helpers (SQLite)
  dedup.py                    # cross-portal de-duplication
  commute.py                  # offline drive/PT estimate
  analysis.py                 # offline FR/DE/EN description heuristics
  scoring.py                  # hard filter + price caps + weighted score + prune + Rich shortlist
  finance.py                  # mortgage estimate + buy-vs-rent break-even
  digest.py                   # new-since-last-run, price-drops, days-on-market, buy-vs-rent
  snapshots.py                # daily market aggregates (trend layer)
  timeutil.py                 # shared naive-UTC datetime helpers
  dashboard.py                # Streamlit browser dashboard
  backup.py                   # DB backup / restore (protect the accumulated history)
  __main__.py                 # CLI: run / shortlist / dashboard / backup / restore / init-db
src/scrapers/luxembourg/
  base.py                     # shared scraper base (upsert, price history)
  athome.py                   # athome.lu (parses window.__INITIAL_STATE__ JSON)
  immotop.py, wortimmo.py     # parked (bot-blocked)
  __init__.py                 # run_luxembourg() orchestrator
alembic/                      # database migrations
scripts/run_lux.sh            # cron wrapper
tests/                        # 158 tests (pytest)
SETUP.md / RUNBOOK.md / ARCHITECTURE.md   # deeper docs
```

---

## Design decisions

- **No paid APIs.** Commute times use a straight‑line distance + assumed speeds
  model (not Google Maps); description analysis uses keyword heuristics (not an
  LLM). Both are deterministic, free, offline, and "good enough for ranking" — at
  the cost of being approximate (see caveats).
- **SQLite + Alembic.** Zero‑config single‑file database; migrations keep the
  schema reproducible.
- **Validation in Pydantic, flexible strings in the DB.** Adding a new portal never
  requires a database migration.
- **Parse stable data, not fragile markup.** athome is parsed from its embedded
  JSON state rather than CSS classes, which survive site restyles.
- **Resilient runs.** One portal's failure can't break the rest.

---

## What's outstanding

- **Notifications** — a **once‑a‑day Gmail digest** (new matches + price drops) is
  the main thing left to build; until then you check the dashboard or the cron log.
- **immotop.lu & wortimmo.lu** are **parked** — Cloudflare‑walled (would need a
  headless browser) and largely duplicate athome, so the payoff is low. **athome
  alone covers most of the market.**
- **Richer per‑listing detail** — the scraper uses the search‑results JSON, which
  omits energy class (so that subscore stays neutral). Fetching each advert page
  would fill it in, at the cost of many more requests.
- **A furnished‑specialist source** (e.g. HousingAnywhere) would add coverage in
  the one segment the big portals under‑serve.

---

## Caveats

- **Commute is an estimate**, not real routing — great for ranking, not minute‑
  accurate; communes reached mainly by motorway read a little high.
- **Description analysis is heuristic** — it catches common signals, not nuance.
- **Scrapers can break** when a portal changes its page structure. The run will
  just report `scraper_errors` and skip it; re‑validating is documented in
  `RUNBOOK.md`.
- **Be a good citizen.** This is a low‑volume personal tool with polite request
  delays and a descriptive User‑Agent; respect each portal's Terms of Service and
  `robots.txt`, and keep it that way.

---

*Personal project — not affiliated with athome.lu, immotop.lu, wortimmo.lu, or DWS.*
