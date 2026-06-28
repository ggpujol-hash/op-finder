"""Adapter generique pilote par selecteurs CSS (config.yaml).

Convient a la majorite des boutiques JCC en HTML server-rendered. On charge une
page de recherche/categorie, on itere sur les fiches produit, et on extrait
titre / lien / prix / disponibilite.

La logique de parsing (parse_products) est partagee avec l'adapter Playwright,
qui fournit du HTML rendu cote navigateur au lieu du HTML brut.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..config import SiteConfig
from ..models import ProductState, normalize_product_url
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
    # 1. Si on a declare a quoi ressemble "en stock" (bouton panier), ce signal
    #    fait autorite : present -> dispo, absent -> indispo. C'est le plus fiable
    #    (ex. WooCommerce affiche "Ajouter au panier" vs "Lire la suite", et une
    #    precommande "en attente" n'a pas de bouton panier).
    if site.in_stock_selector:
        return bool(node.select_one(site.in_stock_selector))
    # 2. Sinon : indisponible si un marqueur de rupture apparait dans le texte.
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

        url = normalize_product_url(urljoin(site.base_url or site.url, href))
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
                # Classes CSS de la fiche : contiennent souvent un indice de langue
                # (ex. WooCommerce "product_cat-...-francais").
                tags=" ".join(node.get("class") or []),
            )
        )
    return results


def page_url(url: str, page: int, site: SiteConfig) -> str:
    """Build a paginated URL while keeping page 1 equal to the configured URL."""
    if page <= 1:
        return url
    if site.page_style == "path":
        return f"{url.rstrip('/')}/page/{page}/"

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[site.page_param] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _page_number(url: str, site: SiteConfig) -> int | None:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if site.page_param in query and query[site.page_param].isdigit():
        return int(query[site.page_param])
    match = re.search(r"/page/(\d+)/?", parts.path)
    if match:
        return int(match.group(1))
    return None


def pagination_urls(html: str, current_url: str, site: SiteConfig) -> list[str]:
    """Find explicit pagination links in a category page, capped by site.max_pages."""
    soup = BeautifulSoup(html, "html.parser")
    pages: dict[int, str] = {}
    for link in soup.select("a[href]"):
        url = urljoin(current_url, link["href"])
        page = _page_number(url, site)
        if page is None or page <= 1 or page > site.max_pages:
            continue
        parts = urlsplit(url)
        pages.setdefault(page, urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, "")))
    return [pages[p] for p in sorted(pages)]


class GenericHtmlAdapter(Adapter):
    def collect(self) -> list[ProductState]:
        products: list[ProductState] = []
        seen_urls: set[str] = set()
        first_html = self.fetch_html(self.site.url)
        urls = [self.site.url, *pagination_urls(first_html, self.site.url, self.site)]
        for url in urls:
            html = first_html if url == self.site.url else self.fetch_html(url)
            for product in parse_products(html, self.site):
                if product.url in seen_urls:
                    continue
                seen_urls.add(product.url)
                products.append(product)
        return products
