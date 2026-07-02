"""Sonde jetable : recupere une page Cloudflare via FlareSolverr et affiche la
structure (classes repetees + liens produit) pour caler les selecteurs.

Usage (en CI, FlareSolverr sur localhost:8191) :
    FLARESOLVERR_URL=http://localhost:8191/v1 python scripts/probe_flaresolverr.py <URL>
"""
from __future__ import annotations

import os
import sys
from collections import Counter

import httpx
from bs4 import BeautifulSoup


def fetch(url: str, endpoint: str) -> str:
    payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
    resp = httpx.post(endpoint, json=payload, timeout=90.0)
    resp.raise_for_status()
    data = resp.json()
    html = (data.get("solution") or {}).get("response") or ""
    if not html:
        raise SystemExit(f"FlareSolverr KO: {data.get('message')}")
    return html


def main() -> None:
    url = sys.argv[1]
    endpoint = os.environ["FLARESOLVERR_URL"]
    html = fetch(url, endpoint)
    print(f"== HTML {len(html)} octets ==\n")
    soup = BeautifulSoup(html, "html.parser")

    counter: Counter[str] = Counter()
    for el in soup.find_all(True):
        classes = el.get("class")
        if classes:
            counter[f"{el.name}.{'.'.join(classes)}"] += 1
    print("== Blocs repetes (candidats 'item', >=3x) ==")
    for sel, n in counter.most_common(40):
        if n >= 3:
            print(f"  {n:4d}x  {sel}")

    print("\n== Premiers liens produit ==")
    seen = 0
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(t in href.lower() for t in ("product", "produit", ".html", "/p/")):
            print(f"  {a.get_text(strip=True)[:55]!r} -> {href[:80]}")
            seen += 1
            if seen >= 20:
                break

    # Detail stock : pour un selecteur d'item passe en argv[2], dumper chaque
    # produit (titre / prix / bouton panier / etiquettes / mots-cles rupture).
    item_sel = sys.argv[2] if len(sys.argv) > 2 else None
    if item_sel:
        print(f"\n== Produits via '{item_sel}' ==")
        stock_words = ("rupture", "épuisé", "epuise", "indisponible", "stock",
                       "précommande", "precommande", "preco")
        for node in soup.select(item_sel):
            title = node.select_one("h3.nomprod, .nomprod_link, h3, h2")
            price = node.select_one(".prixprod, .prix, .impact_price")
            cart = node.select_one("a.addbasket, .addbasket, .block-btn-addbasket")
            etiq = node.select_one(".container-etiquette, .etiquette")
            low = node.get_text(" ", strip=True).lower()
            hits = [w for w in stock_words if w in low]
            print(f"  - {title.get_text(strip=True)[:45] if title else '?':45} | "
                  f"prix={price.get_text(strip=True) if price else '-':10} | "
                  f"cart={'Y' if cart else 'N'} | "
                  f"etiq={(etiq.get_text(' ',strip=True)[:25] if etiq else '-')!r} | "
                  f"mots={hits}")


if __name__ == "__main__":
    main()
