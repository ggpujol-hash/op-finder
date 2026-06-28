"""Adapter generique pilote par selecteurs CSS (config.yaml).

Convient a la majorite des boutiques JCC en HTML server-rendered. On charge une
page de recherche/categorie, on itere sur les fiches produit, et on extrait
titre / lien / prix / disponibilite.

La logique de parsing (parse_products) est partagee avec l'adapter Playwright,
qui fournit du HTML rendu cote navigateur au lieu du HTML brut.
"""
from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..config import SiteConfig
from ..models import ProductState
from .base import Adapter


def _select_one_text(node, selector: str | None) -> str | None:
    if not selector:
        return None
    # Convention ":self" -> lire le noeud lui-meme (utile quand l'item EST le lien,
    # ex. sites React/MUI ou seules les URLs produit sont stables).
    if selector.strip() == ":self":
        txt = node.get_text(" ", strip=True)
        return txt or None
    for sel in selector.split(","):
        el = node.select_one(sel.strip())
        if el:
            txt = el.get_text(" ", strip=True)
            if txt:
                return txt
    return None


def _select_one_href(node, selector: str | None) -> str | None:
    if not selector:
        return None
    if selector.strip() == ":self":
        return node["href"] if node.has_attr("href") else None
    for sel in selector.split(","):
        el = node.select_one(sel.strip())
        if el and el.has_attr("href"):
            return el["href"]
    # Le node lui-meme est peut-etre un lien.
    if node.name == "a" and node.has_attr("href"):
        return node["href"]
    return None


def _is_available(node, site: SiteConfig) -> bool:
    # 1. Marqueur explicite de dispo (bouton panier/precommande) -> disponible.
    if site.in_stock_selector and node.select_one(site.in_stock_selector):
        return True
    # 2. Marqueur de rupture dans le texte de la fiche -> indisponible.
    text = node.get_text(" ", strip=True).lower()
    for marker in site.out_of_stock_markers:
        if marker in text:
            return False
    # 3. Par defaut : disponible (presence dans les resultats = en vente).
    return True


def parse_products(html: str, site: SiteConfig) -> list[ProductState]:
    """Extrait les produits d'un HTML donne selon les selecteurs du site."""
    soup = BeautifulSoup(html, "html.parser")

    item_sel = site.selectors.get("item")
    if not item_sel:
        raise ValueError(f"{site.name}: selecteur 'item' manquant dans config")

    nodes = []
    for sel in item_sel.split(","):
        nodes = soup.select(sel.strip())
        if nodes:
            break

    results: list[ProductState] = []
    seen_urls: set[str] = set()
    for node in nodes:
        title = _select_one_text(node, site.selectors.get("title"))
        href = _select_one_href(node, site.selectors.get("link"))
        if not title or not href:
            continue

        url = urljoin(site.base_url or site.url, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        results.append(
            ProductState(
                site=site.name,
                title=title,
                url=url,
                price=_select_one_text(node, site.selectors.get("price")),
                available=_is_available(node, site),
            )
        )
    return results


class GenericHtmlAdapter(Adapter):
    def collect(self) -> list[ProductState]:
        html = self.fetch_html(self.site.url)
        return parse_products(html, self.site)
