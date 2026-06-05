"""Probe HousingAnywhere for Luxembourg — is it scrapable, and where's the data?

HousingAnywhere is a furnished/mid-term rental platform — the segment athome
under-serves. Before writing a scraper we need to know, from a real network the
sandbox doesn't have: (a) does a plain request get through (200, not 403), and
(b) is the listing data in an embedded JSON blob / JSON API (like athome) or a
hidden HTML structure?

On the workstation:
  1. Open https://housinganywhere.com, search "Luxembourg", copy the results URL.
  2. python scripts/probe_housinganywhere.py "<that results URL>"
  3. Paste the output back so the scraper can target the right structure.
"""

from __future__ import annotations

import re
import sys

import httpx

UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en;q=0.9,fr;q=0.8",
}


def probe(url: str) -> None:
    print(f"\n=== {url}")
    try:
        r = httpx.get(url, headers=UA, follow_redirects=True, timeout=60)
    except httpx.HTTPError as exc:
        print(f"  fetch failed: {type(exc).__name__}: {exc}")
        return
    html = r.text
    print(f"  HTTP {r.status_code}, {len(html)} bytes, final={r.url}")

    markers = {
        "__NEXT_DATA__": html.count("__NEXT_DATA__"),
        "__NUXT__": html.count("__NUXT__"),
        "__APOLLO_STATE__": html.count("__APOLLO_STATE__"),
        "window.__": len(re.findall(r"window\.__[A-Z_]+", html)),
        "application/ld+json": html.count("application/ld+json"),
        '"price"': html.count('"price"'),
        '"bedrooms"': html.count('"bedrooms"'),
        "challenge/cloudflare": len(re.findall(r"cloudflare|just a moment|cf-chl|captcha", html, re.I)),
    }
    print("  markers:", {k: v for k, v in markers.items() if v})
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    if title:
        print("  title:", title.group(1).strip()[:120])

    # If Next.js, show the top-level keys of its data island (best case).
    m = re.search(r'__NEXT_DATA__[^>]*>(\{.*?\})</script>', html, re.S)
    if m:
        import json
        try:
            data = json.loads(m.group(1))
            print("  __NEXT_DATA__ top keys:", list(data.keys()))
            page = data.get("props", {}).get("pageProps", {})
            print("  pageProps keys:", list(page.keys())[:20])
        except Exception as exc:
            print("  __NEXT_DATA__ parse failed:", exc)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    for u in sys.argv[1:]:
        probe(u)
