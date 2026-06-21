# CHEATSHEET — using the Luxembourg property monitor day to day

Copy/paste snippets so you can operate the tool **without asking Claude each time**.
Fuller docs: `README.md` (overview), `RUNBOOK.md` (go-live), `SESSION_HANDOFF.md`
(state for a new dev session).

> **Two things first, every time you open a terminal:**
> ```bash
> cd ~/immo
> source .venv/bin/activate          # your prompt should now start with (.venv)
> ```
> Every Python/CLI snippet below assumes you've done this. Plain `tail`/`grep`
> log checks don't need the venv.

---

## 0. After a code update (do this when you've `git pull`ed new fixes)

Whenever new code has been pushed (like the latest review fixes), bring your box
up to date and rebuild the data so it reflects the new filters/scoring:

```bash
cd ~/immo && source .venv/bin/activate
git pull                              # get the new code
alembic upgrade head                  # apply any new DB migrations (safe, no data loss)
pytest -q                             # sanity: "1 skipped, … passed"
python -m src.lux_monitor run         # in-place refresh (~5 min); applies new filters
```

A plain `run` is **always** the right move after a code/filter change: it updates
listings in place, **prunes** anything that no longer matches, and **keeps your
accumulated history** (price history, days-on-market, trend snapshots).

> 🚫 **Do NOT `rm data/monitor.db`.** Wiping it permanently loses that history and
> resets days-on-market to 0. See **§7** — there's a `backup`/`restore` command if
> you ever need a safety net.

---

## 1. Daily check — "did the 7am run work, and what's new?"

**A. Did it run and succeed?** (no venv needed)
```bash
grep "run_lux done" ~/immo/logs/lux_run.log | tail -3
```
Healthy = three recent dates each ending `(exit 0)`.
- A **missing date** → the PC was asleep/off at 07:00 that day (cron skips it).
- A **non-zero exit** → open the log to see what failed: `tail -40 ~/immo/logs/lux_run.log`

**B. What changed today?**
```bash
python - <<'PY'
from datetime import datetime, timezone, timedelta
from sqlalchemy import func
from src.lux_monitor.db import get_engine, session_scope
from src.lux_monitor.models import Listing, MarketSnapshot
cut = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
try:
    with session_scope(get_engine()) as s:
        print("Last snapshot :", s.query(func.max(MarketSnapshot.snapshot_date)).scalar())
        print("Active listings:", s.query(func.count()).filter(Listing.is_active.is_(True)).scalar())
        print("New in 24h    :", s.query(func.count()).filter(Listing.first_seen_at >= cut).scalar())
except Exception as e:
    print("No data yet — run `python -m src.lux_monitor run` first.", f"({type(e).__name__})")
PY
```
"Last snapshot" = today's date → the run exported and committed.

**C. See the results (terminal):**
```bash
python -m src.lux_monitor shortlist --top 20
python -m src.lux_monitor shortlist --type rent --top 20        # furnished | rent | buy
```

---

## 2. The dashboard (browse on PC or phone)

```bash
python -m src.lux_monitor dashboard       # leave this terminal running; Ctrl-C to stop
```
Open in a browser:
- **PC:** http://localhost:8501
- **Phone (same Wi-Fi):** http://192.168.1.13:8501
  (if that IP changed: `hostname -I | awk '{print $1}'` to get the current one)

In the dashboard, **Screen** (sidebar): rooms ≥, bedrooms ≥, surface m² ≥,
**monthly € ≤** (rent or estimated mortgage), drive min ≤, plus type/commune/min-score.
Sort by score / € / **€/mo** / **€/m²** / days. Sections: **🆕 New only**,
**📉 price drops**, **📈 market trends** + rent-vs-buy compare, **🐌 long on the market**.
Hit **🔄 Reload data** after a new run; **open ↗** to view an advert.

The **€/mo** column puts buy and rent on one axis: for a buy it's the estimated
**mortgage payment** (loan principal+interest). Adjust the rate/term/financing
**live** with the sidebar **"Mortgage (buy €/mo)"** sliders — no re-scrape.
(Defaults come from `MORTGAGE` in `config/luxembourg.py`: 3.5% / 30y / 100%.) It
excludes notaire fees, maintenance and impôt foncier, so true ownership cost is
a bit higher.

The **⚖️ Buy vs rent — per commune** table shows median rent vs median mortgage
and an approximate **break-even (years)** = upfront buying cost (~8% of price) ÷
the monthly rent-minus-mortgage saving. Blank break-even = at this rate, buying
costs more per month than renting (no cash-flow break-even) — slide the rate down
to see where it flips.

> Dashboard needs Streamlit once: `pip install streamlit`

---

## 3. Run it manually (don't need to wait for 7am)

```bash
python -m src.lux_monitor run                 # full harvest + score + snapshot (~5 min)
~/immo/scripts/run_lux.sh; echo "exit=$?"     # same, exactly as cron runs it (logs to file)
```
`python -m src.lux_monitor run --no-scrape`   # re-score existing data, no network (after config edits)

---

## 4. Explain / inspect a single listing

```bash
python -m src.lux_monitor.scoring --explain <listing_id>   # why it got its score
```

---

## 5. Tuning what you search for

Edit **`config/luxembourg.py`** then re-rank existing data (no re-scrape):
```bash
python -m src.lux_monitor run --no-scrape
```
Knobs in that file:
- `TARGET_COMMUNES` — the 7 communes (+ a `COMMUNE_HKEYS` token each for athome).
- `HARD_FILTERS` — rooms 3–8, surface ≥ 80 m².
- `MAX_PRICE_EUR` — buy ≤ €3M, rent ≤ €6000/mo, furnished uncapped.
- `SCORING_WEIGHTS` — must sum to 100.
- `MORTGAGE` — rate %, term years, financing % for the buy **€/mo** estimate
  (used in the dashboard and the terminal shortlist; no re-scrape needed).

> After a **filter** change, the next `run` also **prunes** listings that no longer
> match (deactivates them) — so the DB self-cleans.

---

## 6. Scheduling (already set up: cron daily 07:00)

```bash
crontab -l                       # see the schedule
crontab -e                       # change the time (0 7 * * * = 07:00 daily)
```
**If the PC is often asleep at 7am** (cron silently skips those days), switch to the
systemd timer which catches up on wake: see `scripts/systemd/README.md`. Use only
one — remove the cron line if you switch.

---

## 7. The database — back up, never wipe

One SQLite file holds everything: **`~/immo/data/monitor.db`** — every listing,
its **price history**, and the **daily market snapshots**. Re-scraping CANNOT
recover this history (athome only shows the current market), so the file is
precious.

```bash
python -m src.lux_monitor backup       # snapshot -> data/backups/ (keeps newest 14)
python -m src.lux_monitor restore      # restore the newest backup (asks to confirm)
python -m src.lux_monitor restore --file data/backups/monitor-<ts>.db
```
- The **daily cron backs up automatically** before every run — you're protected.
- Listings that vanish are **deactivated, not deleted** (history kept).
- `market_snapshots_lu` accumulates one row per (date, type, commune) per run —
  your long-run trend.

> 🚫 **Never `rm data/monitor.db`.** It permanently resets the days-on-market clock
> and the trend history to zero. A plain `python -m src.lux_monitor run` already
> refreshes listings in place and prunes non-matches — that's what you want, not a
> wipe. (If you ever *truly* must start over: `backup` first, then delete.)

---

## 8. Keep code up to date / health check

```bash
cd ~/immo && git pull            # get the latest code
alembic upgrade head             # apply any new DB migrations (safe; no data loss)
pytest -q                        # 153 passing = healthy
```

(See **§0** for the full "after a code update" routine, incl. an optional clean
re-scrape.)

---

## 9. Troubleshooting quick map

| Symptom | Check |
|---|---|
| No new data today | `grep "run_lux done" ~/immo/logs/lux_run.log \| tail -3` — missing date = PC asleep at 7am |
| Run errored | `tail -40 ~/immo/logs/lux_run.log` — read the lines above the failing `done` |
| Counts look wrong / thin | look at the `athome funnel …` lines in the log: `total=` vs `kept=` per commune |
| Dashboard won't load on phone | use `http://<PC-LAN-IP>:8501` (not localhost); IP via `hostname -I` |
| `ModuleNotFoundError` | you forgot `source .venv/bin/activate` |
| `snapshot stage failed` in log / no trends | you skipped a migration — run `alembic upgrade head` (the run itself still succeeds; snapshots are non-fatal) |
| athome returns nothing | a commune's `q=` token may be stale — see SESSION_HANDOFF "How athome scraping works" |

---

*No push alerts yet — you check the log/dashboard. A Gmail/Telegram daily digest is
the planned next step (`digest.py` already computes new + price-drops + slow-movers).*
