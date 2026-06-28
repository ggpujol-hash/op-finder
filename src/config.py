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
    in_stock_selector: str | None = None
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


def load_config(path: str | Path | None = None) -> AppConfig:
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    defaults = raw.get("defaults", {})
    keywords = [k.lower() for k in defaults.get("keywords", [])]
    hot_keywords = [k.lower() for k in defaults.get("hot_keywords", [])]
    exclude_keywords = [k.lower() for k in defaults.get("exclude_keywords", [])]
    exclude_lang_codes = [k.lower() for k in defaults.get("exclude_lang_codes", [])]

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
                in_stock_selector=s.get("in_stock_selector"),
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
        site_keywords={s.name: s.keywords for s in sites if s.keywords is not None},
        sites=sites,
    )
