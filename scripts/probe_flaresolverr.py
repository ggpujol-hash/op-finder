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
    # Diagnostic detaille sur un produit anglais qui DEVRAIT etre garde.
    from src.detector import _matches, _is_excluded_type, _has_lang_code, _url_has_lang_code
    target = next((p for p in prods if 'op-06' in p.title.lower() and 'anglais' in p.title.lower()), None)
    if target:
        lt = f"{target.title} {target.tags}"
        print("[OP-06] title:", repr(target.title))
        print("[OP-06] url:", target.url)
        print("[OP-06] tags:", repr(target.tags))
        print("[OP-06] keyword_ok:", _matches(target.title, cfg.keywords))
        print("[OP-06] type_excl:", _is_excluded_type(target.title, cfg))
        print("[OP-06] exclude_kw:", _matches(lt, cfg.exclude_keywords))
        print("[OP-06] lang_code(title+tags):", _has_lang_code(lt, cfg.exclude_lang_codes))
        print("[OP-06] url_lang_code:", _url_has_lang_code(target.url, cfg.exclude_lang_codes))
        print("[OP-06] kept alone:", len(apply_filters([target], cfg)) == 1)


if __name__ == "__main__":
    main()
