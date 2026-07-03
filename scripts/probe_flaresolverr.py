"""Sonde jetable : compare plain httpx vs FlareSolverr sur des sites qui
renvoient 0 produit en CI (IP datacenter potentiellement discriminee).

Usage (en CI, FlareSolverr sur localhost:8191) :
    FLARESOLVERR_URL=http://localhost:8191/v1 \
      python scripts/probe_flaresolverr.py "<URL>" "<item_selector>"
"""
from __future__ import annotations

import os
import sys

import httpx
from bs4 import BeautifulSoup


def via_httpx(url: str) -> str:
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
    r = httpx.get(url, headers={"User-Agent": ua, "Accept-Language": "fr-FR,fr;q=0.9"},
                  timeout=30.0, follow_redirects=True)
    return f"HTTP {r.status_code} {len(r.text)}o", r.text


def via_flare(url: str, endpoint: str) -> tuple[str, str]:
    payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
    r = httpx.post(endpoint, json=payload, timeout=90.0)
    r.raise_for_status()
    data = r.json()
    sol = data.get("solution") or {}
    return f"status={sol.get('status')} {len(sol.get('response') or '')}o", sol.get("response") or ""


def count(html: str, sel: str) -> int:
    return len(BeautifulSoup(html, "html.parser").select(sel))


def main() -> None:
    url, sel = sys.argv[1], sys.argv[2]
    endpoint = os.environ["FLARESOLVERR_URL"]
    print(f"URL: {url}\nselector: {sel}\n")
    try:
        meta, html = via_httpx(url)
        print(f"[httpx]        {meta:24} -> {count(html, sel)} items")
    except Exception as e:  # noqa: BLE001
        print(f"[httpx]        ERREUR {e}")
    try:
        meta, html = via_flare(url, endpoint)
        print(f"[flaresolverr] {meta:24} -> {count(html, sel)} items")
    except Exception as e:  # noqa: BLE001
        print(f"[flaresolverr] ERREUR {e}")


if __name__ == "__main__":
    main()
