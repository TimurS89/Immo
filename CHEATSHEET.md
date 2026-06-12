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

In the dashboard: filter by type/commune/min-score, sort (incl. **days** on market),
**🆕 New only** toggle, **📉 price drops**, **📈 market trends** + rent-vs-buy compare,
**🐌 long on the market**. Hit **🔄 Reload data** after a new run. Click **open ↗** to
view an advert.

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

## 7. The database & backups

One SQLite file holds everything: **`~/immo/data/monitor.db`**.
```bash
cp ~/immo/data/monitor.db ~/immo/data/monitor_backup_$(date +%F).db   # back up
```
- Listings that vanish are **deactivated, not deleted** (history kept).
- `market_snapshots_lu` accumulates one row per (date, type, commune) per run —
  this is your long-run trend; don't delete the DB if you want to keep it.
- To start completely fresh (loses all history):
  `rm ~/immo/data/monitor.db && alembic upgrade head`

---

## 8. Keep code up to date / health check

```bash
cd ~/immo && git pull            # get the latest code
alembic upgrade head             # apply any new DB migrations (safe; no data loss)
pytest -q                        # 135 passing = healthy
```

---

## 9. Troubleshooting quick map

| Symptom | Check |
|---|---|
| No new data today | `grep "run_lux done" ~/immo/logs/lux_run.log \| tail -3` — missing date = PC asleep at 7am |
| Run errored | `tail -40 ~/immo/logs/lux_run.log` — read the lines above the failing `done` |
| Counts look wrong / thin | look at the `athome funnel …` lines in the log: `total=` vs `kept=` per commune |
| Dashboard won't load on phone | use `http://<PC-LAN-IP>:8501` (not localhost); IP via `hostname -I` |
| `ModuleNotFoundError` | you forgot `source .venv/bin/activate` |
| athome returns nothing | a commune's `q=` token may be stale — see SESSION_HANDOFF "How athome scraping works" |

---

*No push alerts yet — you check the log/dashboard. A Gmail/Telegram daily digest is
the planned next step (`digest.py` already computes new + price-drops + slow-movers).*
