"""Structures de donnees partagees."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


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
        basis = f"{self.site}|{self.url or self.title}".lower()
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


@dataclass
class Event:
    """Transition detectee, candidate a une alerte."""

    kind: str  # "new" | "restock" | "price_change"
    state: ProductState
    detail: str = ""
