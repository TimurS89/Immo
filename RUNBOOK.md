# RUNBOOK — Going live with the Luxembourg property monitor

A step-by-step guide to take the monitor from "tests pass" to "running on a
schedule" on your workstation. Follow the steps in order.

> **Golden rule:** the scraper selectors are validated only against *synthetic*
> fixtures. **Validate them against real HTML (Step 1) and do one supervised run
> (Step 2) before you schedule anything (Step 4).** Never cron an unvalidated
> scraper — it may silently store nothing.

Everything except scraping is offline (commute is an estimate, analysis is
heuristic), so **no API keys are required**.

---

## Step 0 — One-time setup

Full details in `SETUP.md`; the short version:

```bash
cd Immo
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # httpx + bs4 cover LU scraping (no Playwright needed)
alembic upgrade head             # build data/monitor.db
pytest -q                        # expect: all green (115)
```

`.env` is optional. Set `LUX_MONITOR_DB_URL` only if you want the DB somewhere
other than `data/monitor.db`.

---

## Step 1 — Validate the scraper selectors  ⚠️ the critical gate

The three portals and the code that parses them:

| Portal   | Base URL                  | Parser module                              |
|----------|---------------------------|--------------------------------------------|
| athome   | https://www.athome.lu     | `src/scrapers/luxembourg/athome.py`        |
| immotop  | https://www.immotop.lu    | `src/scrapers/luxembourg/immotop.py`       |
| wortimmo | https://immo.wort.lu      | `src/scrapers/luxembourg/wortimmo.py`      |

Each module has three things to check: `search_url()` (builds the search URL),
`parse_serp()` (finds the listing cards), and `_parse_card()` (pulls fields out
of one card).

**The tight validation loop (per portal — no new code, no network in the parse step):**

1. Print the exact URL the scraper will request:
   ```bash
   python -c "from src.config import load_config; from src.scrapers.luxembourg import AtHomeScraper; print(AtHomeScraper(load_config()).search_url('Strassen','rent',1))"
   ```
2. Fetch that page once, politely, to a temp file:
   ```bash
   curl -A "ImmoLuxMonitor/1.0 (personal property search; low volume)" "<paste-url>" -o /tmp/athome.html
   ```
3. Run the parser against the saved page and eyeball the result:
   ```bash
   python - <<'PY'
   from src.scrapers.luxembourg import AtHomeScraper
   items = AtHomeScraper.parse_serp(open('/tmp/athome.html').read(), 'rent')
   print(len(items), "listings parsed")
   for it in items[:3]:
       print(it.portal_listing_id, it.commune, it.bedrooms, it.surface_m2, it.rent_eur, it.url)
   PY
   ```
   - **0 listings** → `parse_serp`'s card selector is stale. Open `/tmp/athome.html`,
     find a listing card, note its real CSS classes, and update `parse_serp` /
     `_parse_card`. Repeat 3 until you get listings with sane fields.
   - **Listings but blank fields** (e.g. `surface_m2=None`) → fix the field
     selectors in `_parse_card`.

Repeat for `ImmotopScraper` / `WortimmoScraper` (swap the class name, base URL,
and temp file).

**Tip — structured data is more stable than CSS classes.** While you have the
real HTML open, check for an embedded `<script type="application/ld+json">` or a
`__NEXT_DATA__` / `window.__INITIAL_STATE__` JSON blob. If a portal exposes one,
parsing that is far more robust than card classes — worth refactoring the parser
toward it before you rely on the portal long-term.

**(Optional) refresh the test fixtures.** The fixtures in
`tests/fixtures/luxembourg/*_serp_rent.html` are synthetic and the parsing tests
assert specific values from them. If you replace a fixture with a real saved
page, you must also update the expected values in
`tests/test_luxembourg_parsing.py` / `tests/test_luxembourg_scrapers.py`, then:
```bash
pytest tests/test_luxembourg_parsing.py tests/test_luxembourg_scrapers.py -q
```
(If you'd rather not touch the tests, just keep validating against `/tmp` pages
as above — the supervised run in Step 2 is the real proof.)

> Note: only **rent** fixtures exist. Buy parsing shares the same selectors, so
> rent validation covers most of it, but spot-check a buy page too if you care
> about purchases.

---

## Step 2 — Supervised first run

Start tiny and watch it:

```bash
python -m src.lux_monitor run --max-pages 1 -v
```

What to look for in the output / `logs`:
- `GET https://...` lines for each portal (the URLs actually requested).
- per-portal `athome: N new, M updated, K deactivated` lines.
- `pipeline: new=… commute_filled=… analyzed=… scored=… filtered_out=…`
- a printed shortlist table at the end.

Then inspect:
```bash
python -m src.lux_monitor shortlist --top 20          # the ranked shortlist
python -m src.lux_monitor.scoring --explain <ID>      # why a listing scored what it did
```

**Troubleshooting:**

| Symptom | Likely cause | Action |
|---|---|---|
| `new=0`, no `GET` lines | portals disabled / no communes | check `config/luxembourg.py` is loaded (`load_config`) |
| `GET` lines but `new=0` | selectors stale | back to Step 1 |
| many `scored`, `filtered_out=0/low` is fine | — | — |
| `scored=0, filtered_out=high` | filters too tight, or commute missing | `--explain` a few to see the reason; tune Step 3 |
| HTTP 403 / captcha | portal blocking | lower volume, keep the UA, increase delay; consider JSON-LD route |

---

## Step 3 — Tune (optional)

All knobs are plain Python; after editing, re-rank **without re-scraping**:

```bash
python -m src.lux_monitor run --no-scrape
```

- **What to search / accept** — `config/luxembourg.py`:
  `TARGET_COMMUNES`, `HARD_FILTERS_RENT` / `HARD_FILTERS_BUY`, `SCORING_WEIGHTS`.
- **Commute estimate** — `src/lux_monitor/commute.py` (`AVG_DRIVE_KMH`,
  `RUSH_DRIVE_FACTOR`, …). Note Mamer reads ~31 min (a flat speed can't see the
  A6) and passes on PT; adjust the constants if that bothers you.
- **Description heuristics** — `src/lux_monitor/analysis.py` (keyword rules,
  penalties/bonuses).

---

## Step 4 — Schedule it (cron)

Only once Steps 1–2 look right. `scripts/run_lux.sh` activates the venv, runs the
full pipeline, and appends to `logs/lux_run.log`.

```bash
chmod +x scripts/run_lux.sh        # once
crontab -e
```
```cron
# every day at 07:15
15 7 * * *  /ABSOLUTE/PATH/TO/Immo/scripts/run_lux.sh
```

Politeness: the default is `max_pages_per_source = 20` (≈ a few hundred requests
across 8 communes × 2 types × 3 portals) with a ~1.5 s delay between requests.
Lower `max_pages_per_source` in `config/luxembourg.py`'s `ScrapersConfig` usage,
or run less often, if you want a lighter footprint. Keep the descriptive
User-Agent.

---

## Step 5 — Operating it

- **See results:** `python -m src.lux_monitor shortlist --top 20`
  (add `--type rent` or `--type buy`).
- **What the DB tracks:** new/updated/delisted listings, full price history, and
  cross-portal duplicates (only the primary is scored). Re-running is safe and
  incremental — enrichment and any user fields are never clobbered by a re-scrape.
- **No push alerts yet:** a scheduled run won't message you; check the shortlist
  or `logs/lux_run.log`. (Notifications can be added later.)
- **When a site changes** (selectors stop returning data): repeat Step 1 for that
  portal. Sites change layouts periodically — budget for occasional re-validation.

---

## Caveats (known, by design)

- Commute is a **crude offline estimate**, not real routing — good for ranking,
  not minute-accurate; highway-reached communes read high.
- Description analysis is **keyword heuristics**, not an LLM — it catches common
  FR/DE/EN signals, not nuance.
- **Notifications, buy fixtures, and a dashboard are not built.**
- Respect each portal's Terms of Service and `robots.txt`; this is a low-volume
  personal tool — keep it that way.

---

## Go-live checklist

- [ ] `pip install -e ".[dev]"` and `alembic upgrade head` done; `pytest -q` green
- [ ] Step 1 selectors validated for **athome**, **immotop**, **wortimmo** (real pages parse to listings with sane fields)
- [ ] `run --max-pages 1` produced real listings and a shortlist
- [ ] `shortlist` / `--explain` look sensible; filters/weights tuned to taste
- [ ] `scripts/run_lux.sh` runs by hand and writes `logs/lux_run.log`
- [ ] crontab entry added with an **absolute** path
- [ ] a plan to glance at the shortlist/log regularly (until alerts exist)
