"""Probe an athome.lu *detail* page to find where the energy class (A–I) lives.

The search-results JSON has no energy class, so filling that scoring subscore
needs the per-listing detail page. Run this on the workstation against a couple of
real advert URLs (copy them from a shortlist / the dashboard 'open' links), and
paste the output back so the enrichment parser can target the right field.

    python scripts/probe_detail.py "https://www.athome.lu/<...>/id-XXXX.html"
"""

from __future__ import annotations

import json
import re
import sys

import httpx

UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-LU,fr;q=0.9,en;q=0.8",
}
# Luxembourg energy passport classes A..I (what we want to extract).
CLASS_RE = re.compile(r"\b([A-I])\b")


def probe(url: str) -> None:
    print(f"\n=== {url}")
    try:
        r = httpx.get(url, headers=UA, follow_redirects=True, timeout=60)
    except httpx.HTTPError as exc:
        print(f"  fetch failed: {type(exc).__name__}: {exc}")
        return
    print(f"  HTTP {r.status_code}, {len(r.text)} bytes")
    html = r.text

    # 1) embedded state blob?
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*", html)
    if m:
        blob = re.sub(r"(?<=[:,\[])\s*(undefined|NaN|-?Infinity)\b", "null", html[m.end():])
        try:
            state = json.JSONDecoder().raw_decode(blob)[0]
            print("  __INITIAL_STATE__: parsed OK")
            # show any key path containing 'energy'/'passe'/'dpe'/'consum'
            hits = []

            def walk(o, path=""):
                if isinstance(o, dict):
                    for k, v in o.items():
                        if re.search(r"energy|energie|passe|dpe|consum|co2|class", str(k), re.I):
                            hits.append((f"{path}.{k}", v if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)[:200]))
                        walk(v, f"{path}.{k}")
                elif isinstance(o, list) and o:
                    walk(o[0], path + "[0]")

            walk(state)
            for p, v in hits[:30]:
                print(f"     {p} = {v}")
            if not hits:
                print("     (no energy-ish keys found in state)")
        except json.JSONDecodeError as exc:
            print(f"  __INITIAL_STATE__ parse failed: {exc}")
    else:
        print("  no __INITIAL_STATE__ on the detail page")

    # 2) ld+json blocks
    lds = re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S)
    print(f"  ld+json blocks: {len(lds)}")

    # 3) raw HTML hints: lines mentioning energy/passeport
    print("  --- HTML lines mentioning energy/passeport (first 8) ---")
    for line in html.splitlines():
        if re.search(r"energie|energy|passeport|classe|dpe|kwh", line, re.I):
            s = line.strip()
            if 0 < len(s) < 240:
                print(f"     {s}")
    # crude cap via separate counter
    # (kept simple; eyeball the output)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    for u in sys.argv[1:]:
        probe(u)
