"""Chargement de la configuration : .env + config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass
class SiteConfig:
    name: str
    type: str
    url: str
    base_url: str = ""
    enabled: bool = True
    interval_seconds: int = 180
    selectors: dict[str, str] = field(default_factory=dict)
    keywords: list[str] | None = None
    max_pages: int = 10
    page_param: str = "page"
    page_style: str = "query"
    out_of_stock_markers: list[str] = field(default_factory=list)
    preorder_markers: list[str] = field(default_factory=list)
    in_stock_selector: str | None = None
    # Si True : la boutique flague fiablement ses ruptures (ex. PrestaShop avec un
    # flag "Out-of-Stock" sur chaque carte epuisee). L'absence de marqueur vaut
    # alors "en stock confirme" plutot que "non confirme" (inferred).
    oos_markers_reliable: bool = False
    # Si True : boutique protegee par Cloudflare. Quand FLARESOLVERR_URL est defini
    # (en CI), on recupere la page via FlareSolverr (resolution du challenge JS).
    # Sinon (ex. en local depuis une IP residentielle), fetch direct normal.
    unblock: bool = False
    # Langue par defaut de la boutique (ex. "fr"). Optionnel : si renseigne ET
    # present dans exclude_lang_codes, on ne garde de ce site QUE les produits
    # explicitement tagues dans une langue voulue (cf. include_lang_codes) — utile
    # pour un shop FR dont les titres anglais sont marques "ENG" mais pas les FR.
    lang: str = ""
    # Marqueurs d'exclusion (langue) du filtre global a NE PAS appliquer sur ce site.
    # Utile quand un mot-cle FR global est en fait present dans TOUS les titres du
    # site, y compris les produits anglais voulus (ex. Philibert prefixe chaque
    # fiche par "One Piece Le Jeu de Cartes", ce qui ferait tomber "jeu de cartes"
    # sur ses displays EN). Soustractif : le reste du filtre global s'applique.
    exclude_keywords_skip: list[str] = field(default_factory=list)
    # Specifiques a l'adapter playwright_html :
    wait_for: str | None = None   # selecteur CSS a attendre avant de lire la page
    wait_ms: int = 2500           # attente supplementaire (ms) apres chargement
    scroll: bool = False          # scroller la page pour declencher le lazy-load


@dataclass
class AppConfig:
    telegram_token: str
    telegram_chat_id: str
    check_interval: int
    check_jitter: int
    keywords: list[str]
    hot_keywords: list[str]
    exclude_keywords: list[str]
    exclude_lang_codes: list[str]
    site_keywords: dict[str, list[str]]
    sites: list[SiteConfig]
    # Champs optionnels (defauts) : rajoutes apres coup, places en fin pour ne pas
    # casser les constructions par position et rester retro-compatibles.
    include_lang_codes: list[str] = field(default_factory=list)
    site_lang: dict[str, str] = field(default_factory=dict)
    # name -> marqueurs d'exclusion globaux a ignorer pour ce site (soustractif).
    site_exclude_skip: dict[str, list[str]] = field(default_factory=dict)
    # Exclusion par TYPE de produit (boosters a l'unite, sleeves, starter decks).
    # `exclude_type_terms` : exclu si le titre contient l'un de ces termes.
    # `booster_unit_markers` : termes de "booster a l'unite" -> exclus SEULEMENT si
    # aucun `bulk_markers` (box/display/boite...) n'est present, pour garder les
    # Displays / Booster Box. Voir detector._is_excluded_type.
    exclude_type_terms: list[str] = field(default_factory=list)
    booster_unit_markers: list[str] = field(default_factory=list)
    bulk_markers: list[str] = field(default_factory=list)


def load_config(path: str | Path | None = None) -> AppConfig:
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    defaults = raw.get("defaults", {})
    keywords = [k.lower() for k in defaults.get("keywords", [])]
    hot_keywords = [k.lower() for k in defaults.get("hot_keywords", [])]
    exclude_keywords = [k.lower() for k in defaults.get("exclude_keywords", [])]
    exclude_lang_codes = [k.lower() for k in defaults.get("exclude_lang_codes", [])]
    include_lang_codes = [
        k.lower()
        for k in defaults.get("include_lang_codes", ["en", "eng", "english", "anglais"])
    ]
    exclude_types = defaults.get("exclude_types", {}) or {}
    exclude_type_terms = [t.lower() for t in exclude_types.get("terms", [])]
    booster_unit_markers = [t.lower() for t in exclude_types.get("single_unit", [])]
    bulk_markers = [t.lower() for t in exclude_types.get("bulk_markers", [])]

    sites: list[SiteConfig] = []
    for s in raw.get("sites", []):
        sites.append(
            SiteConfig(
                name=s["name"],
                type=s.get("type", "generic_html"),
                url=s["url"],
                base_url=s.get("base_url", ""),
                enabled=s.get("enabled", True),
                interval_seconds=int(s.get("interval_seconds", 180)),
                selectors=s.get("selectors", {}) or {},
                keywords=([k.lower() for k in s["keywords"]] if "keywords" in s else None),
                max_pages=int(s.get("max_pages", 10)),
                page_param=s.get("page_param", "page"),
                page_style=s.get("page_style", "query"),
                out_of_stock_markers=[m.lower() for m in s.get("out_of_stock_markers", [])],
                preorder_markers=[m.lower() for m in s.get("preorder_markers", [])],
                in_stock_selector=s.get("in_stock_selector"),
                oos_markers_reliable=bool(s.get("oos_markers_reliable", False)),
                unblock=bool(s.get("unblock", False)),
                lang=str(s.get("lang", "")).lower(),
                exclude_keywords_skip=[k.lower() for k in s.get("exclude_keywords_skip", [])],
                wait_for=s.get("wait_for"),
                wait_ms=int(s.get("wait_ms", 2500)),
                scroll=s.get("scroll", False),
            )
        )

    return AppConfig(
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        check_interval=int(os.getenv("CHECK_INTERVAL_SECONDS", "180")),
        check_jitter=int(os.getenv("CHECK_JITTER_SECONDS", "45")),
        keywords=keywords,
        hot_keywords=hot_keywords,
        exclude_keywords=exclude_keywords,
        exclude_lang_codes=exclude_lang_codes,
        include_lang_codes=include_lang_codes,
        site_keywords={s.name: s.keywords for s in sites if s.keywords is not None},
        site_lang={s.name: s.lang for s in sites if s.lang},
        site_exclude_skip={
            s.name: s.exclude_keywords_skip for s in sites if s.exclude_keywords_skip
        },
        exclude_type_terms=exclude_type_terms,
        booster_unit_markers=booster_unit_markers,
        bulk_markers=bulk_markers,
        sites=sites,
    )
