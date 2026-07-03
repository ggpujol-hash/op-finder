"""Sonde jetable : execute le VRAI adapter (collect) en CI pour reproduire le
bug de pagination Poke-Geek. Affiche le chemin pagination utilise et les counts.

Usage : python scripts/probe_flaresolverr.py "<site_name>"
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.INFO)

from src.config import load_config
from src.adapters.generic_html import GenericHtmlAdapter, pagination_urls, page_url
from src.detector import apply_filters


def main() -> None:
    name = sys.argv[1]
    cfg = load_config("config.yaml")
    site = next(s for s in cfg.sites if s.name == name)
    ad = GenericHtmlAdapter(site)

    first = ad.fetch_html(site.url)
    print(f"[page1] {len(first)}o -> {len(__import__('bs4').BeautifulSoup(first,'html.parser').select(site.selectors['item']))} items")
    links = pagination_urls(first, site.url, site)
    print(f"[pagination_urls] {len(links)} liens: {links[:3]}")
    print(f"[synthetic p2] {page_url(site.url,2,site)}")

    prods = ad.collect()
    kept = apply_filters(list(prods), cfg)
    print(f"[collect] total={len(prods)}  kept={len(kept)}")
    print("[echantillon titres collectes]")
    for p in prods[:20]:
        print("   -", repr(p.title))
    # Diagnostic par etape de filtre sur les produits collectes.
    from src.detector import _matches, _is_excluded_type, _has_lang_code
    kw = cfg.keywords
    n_kw = sum(1 for p in prods if _matches(p.title, kw))
    n_type = sum(1 for p in prods if _is_excluded_type(p.title, cfg))
    n_lang = sum(1 for p in prods if _has_lang_code(f"{p.title} {p.tags}", cfg.exclude_lang_codes))
    n_exkw = sum(1 for p in prods if _matches(f"{p.title} {p.tags}", cfg.exclude_keywords))
    print(f"[filtres] passe keywords={n_kw} | exclus type={n_type} | exclus lang_code={n_lang} | exclus exclude_kw={n_exkw}")


if __name__ == "__main__":
    main()
