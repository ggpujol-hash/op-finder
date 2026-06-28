"""Structures de donnees partagees."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


def normalize_product_url(url: str) -> str:
    """Return a stable product URL for deduplication and display."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, "", ""))


@dataclass
class ProductState:
    """Etat d'un produit observe lors d'un check, avant comparaison avec la base."""

    site: str
    title: str
    url: str
    price: str | None = None
    available: bool = True
    hot: bool = False
    # Texte additionnel pour le filtrage langue (classes CSS de la fiche, etc.),
    # non persiste : sert uniquement a detecter la langue avant stockage.
    tags: str = ""

    @property
    def key(self) -> str:
        """Identifiant stable d'un produit : site + url (fallback titre)."""
        basis = f"{self.site}|{normalize_product_url(self.url) or self.title}".lower()
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


@dataclass
class Event:
    """Transition detectee, candidate a une alerte."""

    kind: str  # "new" | "restock" | "price_change"
    state: ProductState
    detail: str = ""
