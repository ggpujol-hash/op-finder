"""Registre des adapters."""
from __future__ import annotations

from ..config import SiteConfig
from .base import Adapter
from .generic_html import GenericHtmlAdapter


def build_adapter(site: SiteConfig) -> Adapter:
    if site.type == "generic_html":
        return GenericHtmlAdapter(site)
    if site.type == "playwright_html":
        # Import paresseux : evite de charger Playwright si aucun site ne l'utilise.
        from .playwright_html import PlaywrightHtmlAdapter

        return PlaywrightHtmlAdapter(site)
    raise ValueError(f"Type d'adapter inconnu : {site.type!r} (site {site.name})")
