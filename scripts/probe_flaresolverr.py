"""Sonde jetable : simule la pagination sequentielle (comme collect()) et
reporte le nombre d'items par page, pour diagnostiquer un throttle CI.

Usage :
    python scripts/probe_flaresolverr.py "<base_url>" "<item_selector>" "<delay_s>"
"""
from __future__ import annotations

import sys
import time

import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def get(url: str) -> tuple[int, int, str]:
    r = httpx.get(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"},
                  timeout=30.0, follow_redirects=True)
    return r.status_code, len(r.text), r.text


def main() -> None:
    base, sel = sys.argv[1], sys.argv[2]
    delay = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    print(f"base={base}\nselector={sel} delay={delay}s\n")
    for page in range(1, 6):
        url = base if page == 1 else f"{base}?page={page}"
        if page > 1:
            time.sleep(delay)
        try:
            code, size, html = get(url)
            n = len(BeautifulSoup(html, "html.parser").select(sel))
            print(f"page {page}: HTTP {code} {size:>8}o -> {n:3} items  ({url})")
        except Exception as e:  # noqa: BLE001
            print(f"page {page}: ERREUR {e}")


if __name__ == "__main__":
    main()
