"""Structures de donnees partagees."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

# Montant : "119,90" / "1 199,90" / "119" (le dernier d'une chaine = prix courant
# en cas de prix barre + promo).
_PRICE_RE = re.compile(r"\d[\d .]*,\d{2}|\d[\d .]*\d|\d")


def clean_price(value: str | None) -> str | None:
    """Normalise un prix en un montant stable ("119,90 €").

    Retire labels et devises heterogenes ("119,90 EUR", "à partir de 119,90 €",
    espaces insecables) pour que la comparaison prix-a-prix ne declenche pas de
    fausse alerte sur un simple reformatage. Garde le dernier montant (prix
    courant quand un prix barre precede la promo). Source unique reutilisee par
    le parsing, la detection et le dashboard.
    """
    if not value:
        return None
    # Normalise TOUS les espaces (insecable \xa0, fine insecable  , fine
    #  ...) en espace simple : sinon un separateur de milliers exotique coupe
    # le montant ("2 495,00" -> "495,00" en gardant le dernier groupe).
    v = re.sub(r"\s+", " ", value)
    amounts = _PRICE_RE.findall(v)
    if amounts:
        return amounts[-1].strip() + " €"
    return v.strip() or None


def parse_amount(price: str | None) -> float | None:
    """Extrait le montant numerique d'un prix (nettoye ou brut) en float.

    "2 495,00 €" -> 2495.0. Renvoie None si aucun montant lisible. Partage par le
    scaling (scale_price) et la detection de variation significative (detector)."""
    if not price:
        return None
    match = re.search(r"\d[\d ]*(?:,\d+)?", price)
    if not match:
        return None
    try:
        return float(match.group(0).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def scale_price(price: str | None, multiplier: float = 1.0) -> str | None:
    """Applique un facteur au prix deja nettoye (clean_price) et le reformate.

    Sert quand la SOURCE renvoie le prix dans un mauvais contexte de taxe : Oupi,
    fetche via FlareSolverr depuis une IP CI hors-FR, rend du HT -> multiplier 1.2
    reconstitue le TTC affiche aux vrais visiteurs francais. Laisse le prix tel
    quel si multiplier vaut 1.0 ou si le montant n'est pas lisible.
    """
    if not price or multiplier == 1.0:
        return price
    value = parse_amount(price)
    if value is None:
        return price
    scaled = value * multiplier
    # Format francais : espace pour les milliers, virgule pour les decimales.
    formatted = f"{scaled:,.2f}".replace(",", " ").replace(".", ",")
    return f"{formatted} €"


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
    stock_status: str = "inferred"  # "confirmed" | "preorder" | "inferred" | "out"
    hot: bool = False
    # Texte additionnel pour le filtrage langue (classes CSS de la fiche, etc.),
    # non persiste : sert uniquement a detecter la langue avant stockage.
    tags: str = ""

    def __post_init__(self) -> None:
        if self.stock_status == "out":
            self.available = False
        elif not self.available:
            self.stock_status = "out"

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
