# ADAPTATION_PLAN.md — Retargeting Immo to Luxembourg

> Companion to `ARCHITECTURE.md`. This plan classifies every module by how much
> work the **Germany+France → Luxembourg** move requires, then lists concrete
> changes. **No feature code is written yet** — this is the planning artifact.

## TL;DR

The architecture is well-layered, so the move is mostly **(a) write new
Luxembourg portal scrapers** and **(b) decouple a handful of "agnostic" layers
from the hard-coded `DE`/`FR` assumptions** (enums, config field names, a few
presentation loops). The **entire analysis, persistence-engine, reporting-format,
and notification stack is reusable as-is.** The biggest single decision is the
**`country`/`source` enum rigidity** in the schema, because there are no
migrations.

| Classification | Modules | Rough effort |
|----------------|---------|--------------|
| **Keep unchanged** | `database/db.py`, `analysis/*`, `reports/pdf.py`, `reports/email_sender.py`, `scrapers/captcha.py`, `scrapers/base.py` upsert logic | none |
| **Refactor w/ parameterization** | `database/models.py` (enums), `config.py`, `base.py:get_search_areas`, `browser.py` locales, `reports/generator.py`, `reports/charts.py`, `dashboard/app.py`, templates, `main.py` registry | small–medium |
| **Replace entirely** | all 9 `scrapers/germany/*` + `scrapers/france/*`, the search-config geography, the Baden-Baden/Alsace labels | medium–large (new portals) |

---

## 1. Reusability assessment (per module)

| Module | Verdict | Why |
|--------|---------|-----|
| `database/db.py` | **Keep** | Pure SQLite engine/session plumbing. Country-agnostic. |
| `database/models.py` | **Parameterize** | Table shapes are fine; only the `country` & `source` **Enums** must open up for `LU` + LU portals. |
| `config.py` | **Parameterize** | Logic is generic; the *named* `search_germany`/`search_france` fields and the `search["germany"]`/`["france"]` reads are country-bound. |
| `scrapers/base.py` | **Mostly keep** | `PropertyData`, `save_results`, delays, `_calc_price_per_sqm` reusable. Only `get_search_areas()` (DE/FR branch) needs generalizing. |
| `scrapers/browser.py` | **Parameterize** | Stealth/proxy logic reusable; add `LU` to the `LOCALES` map. |
| `scrapers/captcha.py` | **Keep** | Generic 2Captcha wrapper. |
| `scrapers/germany/*` (4) | **Replace** | Sites don't serve Luxembourg; region baked into URLs. |
| `scrapers/france/*` (5) | **Replace** | Same. (SeLoger/Bien'ici *technically* cover LU pages, but URL/zone logic is Alsace-specific — treat as new scrapers.) |
| `analysis/deduplication.py` | **Keep** | Operates on rows; postal-code grouping + fuzzy match work for any country. |
| `analysis/market_stats.py` | **Keep** | Groups by `country` value generically. |
| `analysis/price_tracker.py` | **Keep** | Country-agnostic. |
| `reports/generator.py` | **Parameterize** | Generic except the hard-coded `[(DE,…),(FR,…)]` section loop. |
| `reports/charts.py` | **Parameterize** | Generic Plotly; DE/FR titles/colors hard-coded. |
| `reports/pdf.py` | **Keep** | WeasyPrint wrapper, format-only. |
| `reports/email_sender.py` | **Keep** | Gmail SMTP, transport-only. |
| `reports/templates/*` | **Parameterize** | Only the geography header strings are hard-coded. |
| `dashboard/app.py` | **Parameterize** | Country selectbox + trend loop hard-code DE/FR. |
| `main.py` | **Parameterize** | Orchestration reusable; the **scraper registry** must list LU scrapers instead. |

---

## 2. KEEP UNCHANGED

These ship to Luxembourg with **zero code changes**:

- **`src/database/db.py`** — engine/session/pragmas.
- **`src/analysis/deduplication.py`** — fuzzy cross-source dedup. (Optionally
  re-tune `ADDRESS_SIMILARITY_THRESHOLD` etc. for LU multilingual addresses, but
  no structural change.)
- **`src/analysis/market_stats.py`** and **`src/analysis/price_tracker.py`**.
- **`src/reports/pdf.py`** (WeasyPrint).
- **`src/reports/email_sender.py`** (Gmail SMTP). EUR currency, so report numbers
  need no conversion.
- **`src/scrapers/captcha.py`** (2Captcha).
- **`src/scrapers/base.py` — everything except `get_search_areas()`**:
  `PropertyData`, `save_results()` upsert + price-history, `random_delay`,
  `get_filters`, `_calc_price_per_sqm`. This is the reusable scraper backbone the
  new LU scrapers will subclass.

> The whole **pipeline orchestration pattern** (scrape → dedup → snapshot →
> report → email, with resilient per-source error handling and the
> active/inactive sweep) is reused unchanged in spirit.

---

## 3. REFACTOR WITH PARAMETERIZATION

The goal: make "country" a **data value**, not a hard-coded identifier, so adding
Luxembourg (or any country) is config + a scraper, not a code edit in 15 files.

### 3.1 Schema enums → open up `country` and `source` *(highest priority)*
`src/database/models.py`
- `country` Enum `("DE","FR")` → must allow `"LU"`. `source` Enum (9 fixed names)
  → must allow LU portal names (`athome`, `immotop`, `wortimmo`, …).
- **Recommended:** replace both `Enum(...)` columns with `String` + an
  application-level allow-list (a plain Python `set`/`Enum` constant in code), and
  rely on the existing unique `(source, external_id)` index. This:
  - removes the rigid CHECK constraint that blocks new values,
  - decouples the DB from the portal list (new portals = no schema change),
  - **also fixes the failing test** (see §6), which inserts `source="test"`.
- **If enums are kept** instead: a one-off migration/rebuild is required because
  there are **no Alembic migrations** and SQLite bakes enum CHECKs at table
  creation (see `ARCHITECTURE.md` §7). Simplest path for a personal tool: bump the
  enum definitions and **recreate the DB** (data is re-scrapeable).

### 3.2 Config: generalize geography
`src/config.py`
- Replace the two named fields `search_germany` / `search_france` with a single
  **country-keyed map**, e.g. `search: dict[str, list[SearchArea]]` keyed by
  country code (`"LU"`, `"DE"`, …). Update `load_config` to read `search` generically
  instead of `search["germany"]`/`["france"]`.
- Generalize `SearchArea`: it already has `city`, `postal_codes`, `radius_km`,
  `departments`. For LU, add a generic `regions`/`zones`/`communes` list (or rename
  `departments` → generic `zones`) — Luxembourg searches by **commune/canton or
  country-wide**, not by department. Keep backward-compatible defaults.

### 3.3 `base.py`: generic search-area lookup
`src/scrapers/base.py`
- Replace the `if self.COUNTRY == "DE"` branch in `get_search_areas()` with a
  lookup into the new country-keyed config map: `return self.config.search.get(self.COUNTRY, [])`.

### 3.4 `browser.py`: add LU locale
`src/scrapers/browser.py`
- Add `"LU": {"locale": "fr-LU", "timezone": "Europe/Luxembourg"}` to `LOCALES`
  (Luxembourg portals are primarily French, often German/English too — `fr-LU` is a
  sensible default; consider per-scraper override).

### 3.5 Presentation: drive country sections from data, not literals
- `reports/generator.py` — replace `[("DE","Germany (Baden-Baden)"),("FR",…)]`
  with iteration over countries/labels derived from config (or distinct DB values).
- `reports/charts.py` — generate subplots/series per country present in the data;
  drop the 2-column DE/FR assumption and the per-country hard-coded colors.
- `dashboard/app.py` — populate the country selectbox and the trend loop from the
  distinct `country` values in the DB instead of `["All","DE","FR"]`.
- `reports/templates/report.html` & `pdf_report.html` — make the header
  geography string a template variable.

### 3.6 `main.py`: registry from config
`src/main.py`
- `_register_scrapers()` and `SCRAPER_REGISTRY` should register the LU scrapers.
  Keep the existing lazy-import pattern; just point it at the new modules (and drop
  or keep DE/FR behind config flags as desired).

> **Suggested sequencing:** 3.1 → 3.2/3.3 first (unblocks storing LU rows at all),
> then write scrapers (§4), then 3.5 presentation polish last.

---

## 4. REPLACE ENTIRELY

### 4.1 The 9 existing portal scrapers
`src/scrapers/germany/*` and `src/scrapers/france/*` — none target Luxembourg and
each has region hard-coded into URL templates / location-id maps. They are not
re-pointable. **Action:** create a new `src/scrapers/luxembourg/` package with one
module per LU portal, each subclassing the (reused) `BaseScraper` and emitting
`PropertyData(country="LU", source="<portal>")`.

> Keep the old DE/FR modules in-tree if cross-border search is ever wanted; just
> disable them in `scrapers.enabled`. Otherwise remove to reduce maintenance.

### 4.2 Candidate Luxembourg portals *(to validate before building)*

| Portal | URL | Notes |
|--------|-----|-------|
| **atHome** | athome.lu | Market leader; primary target. Verify JS/anti-bot + whether a JSON/`__NEXT_DATA__`-style payload exists. |
| **Immotop.lu** | immotop.lu | AVIV-group portal (same family as Immowelt/ImmoScout elsewhere) — the Immowelt `__NEXT_DATA__` approach *may* be adaptable. |
| **Wortimmo** | wortimmo.lu | Luxemburger Wort listings. |
| **Editus / Habiter** | editus.lu | Directory + listings. |
| **Luxbazar** | luxbazar.lu | Classifieds (broader, noisier). |

*(These are starting points from general knowledge; confirm coverage, terms of
service, and structure before implementing. Pick 2–3 to start, mirroring the
original "priority order" approach in `PLAN.md`.)*

### 4.3 Geography/config content to replace
- The search config (`config.example.yaml`): drop `germany: Baden-Baden` /
  `france: 67,68`; add Luxembourg areas (e.g. country-wide, or specific
  communes/cantons + LU 4-digit postal codes `L-XXXX`).
- All "Baden-Baden (DE) & Alsace (FR)" labels (report/PDF/dashboard headers).

### 4.4 Luxembourg-specific data nuances to handle in the new scrapers
- **Postal codes:** 4 digits, `L-XXXX` prefix — normalize when parsing/storing
  (the `address_postal_code` column is a free `String(20)`, so no schema change).
- **Languages:** listings appear in **French / German / (some) English /
  Luxembourgish**. Number/price parsing and feature keywords ("chambres"/"Zimmer",
  "pièces", "ascenseur"/"Aufzug") are per-scraper concerns — handle in each module.
- **Energy passport:** LU uses a dual-class energy passport (A–I);
  `energy_rating` is a free string, so store as-is.
- **Property types:** LU `studio/duplex/penthouse` collapse to `apartment`;
  `apartment/house/land` enum values are sufficient.
- **No currency change:** EUR throughout.

---

## 5. Dependency & environment notes — Ubuntu 24.04 (Noble) / Python 3.11

Verified runtime on this machine: **Ubuntu 24.04.4 LTS, Python 3.11.15**
(`/usr/local/bin/python3`; system default is 3.12). No virtualenv existed; only
`Jinja2` and `PyYAML` were pre-installed. Findings and recommended updates:

| Dependency | Pin | Ubuntu 24.04 status / action |
|------------|-----|------------------------------|
| **playwright** | `>=1.40,<2.0` | ⚠️ **Bump.** Reliable `playwright install-deps` support for **Noble (24.04)** landed in newer 1.4x releases; `1.40` predates it. Pin to a recent **1.4x** and run `playwright install --with-deps chromium`. Needs system libs (fonts, nss, etc.). |
| **playwright-stealth** | `>=1.0.6,<2.0` | ✅ Keep `<2.0`. Code uses the **1.x** `stealth_async(page)` API; **2.x renamed it to a `Stealth` class** and would break `browser.py`. Do not let it float to 2.x without a code change. |
| **weasyprint** | `>=60,<70` | ⚠️ **System libs required:** `libpango-1.0-0`, `libpangocairo-1.0-0`, `libcairo2`, `libgdk-pixbuf-2.0-0`, `libffi-dev`, `libharfbuzz` (`apt-get install`). 24.04's Pango is new enough for WeasyPrint 60+. Without these, `pdf.py` fails the import (it degrades gracefully to "no PDF"). Consider bumping to WeasyPrint 62/63. |
| **kaleido** | `>=0.2,<1.0` | ⚠️ `0.2.x` is finicky on newer Linux (static Chromium). Either keep `0.2.1` exactly or move to the **`kaleido` v1** line (different API, needs the Plotly bump). Only needed for *static* PNG export; current charts use `to_html`, so kaleido may be effectively unused — **verify and possibly drop**. |
| **plotly** | `>=5.18,<6.0` | ✅ Fine on 3.11/3.12. (Bump only if moving to kaleido v1.) |
| **lxml** | `>=4.9,<6.0` | ✅ Wheels available; fine. |
| **thefuzz[speedup]** | `>=0.20,<1.0` | ✅ `speedup` pulls `python-Levenshtein` (needs build toolchain or wheel) — installs cleanly on 24.04. |
| **sqlalchemy** | `>=2.0,<3.0` | ✅ Verified **2.0.50** works. |
| **pydantic** | `>=2.5,<3.0` | ✅ Verified **2.13.4** works. |
| **streamlit** | `>=1.29,<2.0` | ✅ Works; large dep tree. Bump to a current 1.3x/1.4x for 3.11/3.12 wheels. |
| **alembic** | `>=1.13,<2.0` | ⚠️ **Declared but unused** (no migrations). Either wire it up (recommended if you keep enums) or drop it. |
| **pytest / pytest-asyncio** | `>=7.4 / >=0.23` | ✅ Verified with pytest **9.0.3**; async test passed. |

**General Ubuntu 24.04 setup recommendations**
- Use a **virtualenv** (PEP 668 marks the system Python "externally managed";
  installing into it needs `--break-system-packages`). Prefer `python -m venv .venv`
  or `uv`.
- Add a one-shot system-deps step to `PIPELINE.md`:
  `playwright install --with-deps chromium` and the WeasyPrint `apt` libs above.
- Pin in a lockfile (the repo has none). The orchestrator sibling repo uses `uv`;
  consider the same here for reproducibility.

---

## 6. Current test status (task: "run all existing tests")

Ran on this machine after installing test deps
(`pydantic, python-dotenv, sqlalchemy, thefuzz, pytest, pytest-asyncio`):

```
python -m pytest tests/ -v   →   9 passed, 1 failed
```

**The single failure is a real code/schema issue, not an environment problem**
(it reproduces on any machine with SQLAlchemy 2.0):

- `tests/test_analysis.py::test_save_property` builds a `Property` with the
  `_make_property` default **`source="test"`** and commits it. The `source`
  column is an `Enum` of the 9 known portals. SQLAlchemy doesn't validate on write
  (default `validate_strings=False`) but **raises `LookupError` on read-back**
  because `"test"` isn't a valid enum member.
- This is the **same rigidity** that blocks adding Luxembourg portals (§3.1) — the
  failing test is a small live demonstration of it.

**Recommended fix (defer until implementation, per "planning only"):** adopt the
§3.1 change (move `source`/`country` to `String` + app-level allow-list). That both
unblocks LU and makes this test pass. As a stopgap, the test could use a valid
`source` value (e.g. `"immoscout24"`).

The other 9 tests (PropertyData, filters, search-areas, dummy scraper,
dedup logic, template render, CSS) pass. The live portal scrapers have **no test
coverage** — worth adding fixture-based parser tests for the new LU scrapers.

---

## 7. Suggested implementation order (for the *next* phase)

1. **Schema/config decoupling** (§3.1–3.3) + fix the failing test → can store `LU`.
2. **First LU scraper** (atHome) end-to-end through `save_results` → DB.
3. **Presentation parameterization** (§3.5) so reports/dashboard render LU.
4. **Remaining LU scrapers** (Immotop, Wortimmo, …) one at a time.
5. **Env/deps** (§5): venv + bumped playwright + WeasyPrint system libs + lockfile.
6. Decide whether to **retire or keep** the DE/FR scrapers behind config flags.
