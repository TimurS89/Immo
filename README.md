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

1. **Scrapes** the configured Luxembourg portals for rentals and sales in your
   target communes.
2. **Stores** each listing in a local SQLite database, tracking price changes,
   when it first/last appeared, and when it disappears (a useful "rented/sold"
   signal).
3. **De‑duplicates** the same property listed on more than one portal.
4. **Estimates the commute** (drive + public transport, at rush hour) to the
   office, and a walk time to the local school.
5. **Analyzes the description** (FR/DE/EN) for red flags ("to renovate", "viager",
   noisy road…) and highlights ("renovated", "bright", "near transport").
6. **Filters** out anything failing your hard criteria, then **scores** the rest
   0–100 on a weighted blend of your preferences.
7. **Surfaces** a ranked shortlist in the terminal.

> Note: the project was originally a Germany/France property tool and was
> **retargeted to Luxembourg**. The old DE/FR code still exists in the repo but is
> dormant — everything current lives under `src/lux_monitor/` and
> `src/scrapers/luxembourg/`.

---

## Current status

The **entire offline pipeline is built, tested (116 passing tests), and working
end‑to‑end.** Live scraping is the part that depends on the outside world, so its
status is per‑portal:

| Component | Status |
|---|---|
| Database + schema (SQLite, Alembic migrations) | ✅ Done |
| Config: target communes, hard filters, scoring weights | ✅ Done |
| **athome.lu** scraper | ✅ **Working live** (parses the site's embedded JSON) |
| immotop.lu scraper | ⛔ Bot‑blocked (wrong URL + 403) — needs a browser |
| wortimmo.lu scraper | ⛔ Bot‑blocked (403 on the real domain) — needs a browser |
| Cross‑portal de‑duplication | ✅ Done |
| Commute estimate (offline) | ✅ Done |
| Description analysis (offline heuristics) | ✅ Done |
| Hard filter + weighted scoring | ✅ Done |
| Orchestrator CLI (`python -m src.lux_monitor`) | ✅ Done |
| Cron wrapper for scheduling | ✅ Done |
| Push notifications (email / Telegram) | ❌ Not built |
| Web dashboard | ❌ Not built |

**In practice:** a real run today scrapes **athome.lu** (the dominant LU portal),
stores listings, scores them, and prints the shortlist — a genuinely useful
monitor. The other two portals actively block automated requests and are parked
until/unless they're worth the extra effort (see [Outstanding](#whats-outstanding)).

---

## How it works

```
            ┌─────────────────────────── run_luxembourg() ───────────────────────────┐
 portals →  │  scrape → save (+price history) → dedup → commute → analyze → score    │ → shortlist
 (athome…)  └────────────────────────────────────────────────────────────────────────┘
                  │            │                  │           │          │
              httpx +      SQLite          haversine     FR/DE/EN     weighted
              JSON parse   (upsert)         estimate      keywords     0–100
```

- **Resilient by design:** if one portal fails (DNS, 403, a site redesign), it's
  logged and skipped — the other portals and all the offline stages still run.
- **Incremental:** re‑running never clobbers enrichment (commute/score) or your
  own notes; it updates facts, records price changes, and marks vanished listings
  inactive.
- **Offline‑first:** the commute and description analysis are computed locally, so
  the only thing that needs the network is the scrape itself.

### The two-stage selection

1. **Hard filter** (non‑negotiable knockouts) — commune in the target set,
   bedrooms ≥ 4, surface ≥ 100 m², price/rent in band, and the commute within
   limits (acceptable if **either** driving ≤ 30 min **or** public transport ≤ 60
   min at rush hour).
2. **Soft score** (0–100) — a weighted blend of: drive time (20), PT time (15),
   school walk (15), foreign‑resident % of the commune (10), energy class (10),
   crèche walk (5), park walk (5), garage (5), garden (5), description quality (7),
   ground‑floor‑with‑garden (3). Missing data scores *neutral*, never punishing.

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

python -m src.lux_monitor run --max-pages 1   # a small, polite first run
python -m src.lux_monitor shortlist --top 20  # see the ranked results
```

No `.env` or API keys are required. (For the full install with the dormant DE/FR
stack, see `SETUP.md`.)

---

## Using it day to day

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

# Limit request volume (useful for testing)
python -m src.lux_monitor run --max-pages 2
```

**Reading the output:** the run prints a one‑line summary like
`pipeline: new=12 … scored=1 filtered_out=11 scraper_errors=1`. `new` is how many
listings came back; `scored` is how many survived the hard filter; `scraper_errors`
counts portals that failed and were skipped. The shortlist table shows score,
commune, beds, m², price, drive/PT minutes, energy class, garage/garden.

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

- **`TARGET_COMMUNES`** — the 8 communes searched/scored: Luxembourg, Walferdange,
  Bertrange, Strassen, Mamer, Hesperange, Sandweiler, Howald — each with its
  foreign‑resident %, train flag, and school coordinates.
- **`HARD_FILTERS_RENT` / `HARD_FILTERS_BUY`** — bedrooms, surface, price/rent band,
  and the 30‑min‑drive / 60‑min‑PT commute caps.
- **`SCORING_WEIGHTS`** — the soft‑score weights (must sum to 100).
- **`DWS_OFFICE`** — the commute destination (Kirchberg) and the rush‑hour rule
  (Tuesday 08:00).

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
  commute.py                  # offline drive/PT/walk estimate
  analysis.py                 # offline FR/DE/EN description heuristics
  scoring.py                  # hard filter + weighted score + Rich shortlist
  __main__.py                 # the `python -m src.lux_monitor` CLI
src/scrapers/luxembourg/
  base.py                     # shared scraper base (upsert, price history)
  athome.py                   # athome.lu (parses window.__INITIAL_STATE__ JSON)
  immotop.py, wortimmo.py     # parked (bot-blocked)
  __init__.py                 # run_luxembourg() orchestrator
alembic/                      # database migrations
scripts/run_lux.sh            # cron wrapper
tests/                        # 116 tests (pytest)
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

- **immotop.lu & wortimmo.lu** return `403`/`404` to simple HTTP requests (bot
  protection). Supporting them would need real browser automation (Playwright +
  stealth) and may still hit Terms‑of‑Service limits. Parked for now; **athome
  alone covers most of the market.**
- **Notifications** — there's currently no push when a great new listing appears;
  you check the shortlist or the cron log. Email (Gmail) or Telegram could be added.
- **Web dashboard** — browsing/filtering/trends in a UI (a Streamlit app) is
  scaffolded in the legacy stack but not wired to `lux_monitor`.
- **Buy‑side & richer detail** — sales work but are less exercised than rentals;
  per‑listing detail‑page enrichment (energy class, full photos) is not done — the
  scraper currently uses what the search results page provides.

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
