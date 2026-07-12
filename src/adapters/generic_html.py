"""Adapter generique pilote par selecteurs CSS (config.yaml).

Convient a la majorite des boutiques JCC en HTML server-rendered. On charge une
page de recherche/categorie, on itere sur les fiches produit, et on extrait
titre / lien / prix / disponibilite.

La logique de parsing (parse_products) est partagee avec l'adapter Playwright,
qui fournit du HTML rendu cote navigateur au lieu du HTML brut.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import httpx

from ..config import SiteConfig
from ..models import ProductState, clean_price, normalize_product_url, scale_price
from .base import Adapter

log = logging.getLogger("adapter.generic")

# Delai (s) entre deux fetchs de pagination : evite le throttle anti-bot qui
# coupe la pagination sur les IP datacenter (cf. Poke-Geek).
PAGE_FETCH_DELAY = 2.0


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


DEFAULT_PREORDER_MARKERS = (
    "precommande",
    "précommande",
    "pre-order",
    "pre order",
    "preorder",
    "reservation",
    "réservation",
)


def _norm(s: str) -> str:
    """Normalise pour le matching de marqueurs : minuscules + tirets/insecables
    ramenes a des espaces simples. Ainsi "out of stock" matche "Out-of-Stock"."""
    return re.sub(r"[\s \-]+", " ", s.lower()).strip()


def _has_marker(haystack: str, markers) -> bool:
    norm = _norm(haystack)
    return any(_norm(m) in norm for m in markers)


def _has_oos_class(classes) -> bool:
    """Detecte une classe de rupture sur la carte : `outofstock` (WooCommerce) ou
    toute variante contenant "out of stock" apres normalisation des separateurs
    (ex. `product-card--out-of-stock` chez ONY TCG). Signal fiable et independant
    de la langue."""
    return any(_norm(c).replace(" ", "") in ("outofstock",) or "out of stock" in _norm(c)
               for c in classes)


def _stock_status(node, site: SiteConfig, url: str = "") -> str:
    text = node.get_text(" ", strip=True)
    classes = node.get("class") or []
    preorder_markers = [*DEFAULT_PREORDER_MARKERS, *site.preorder_markers]

    # 1. RUPTURE explicite -> "out", PRIORITAIRE sur le label "precommande". Un
    #    produit affiche "Pre-order" mais "Out of stock" (bouton inactif) n'est PAS
    #    commandable : il doit remonter en rupture, pas en precommande. Signaux de
    #    rupture, par fiabilite : classe WooCommerce `outofstock`, marqueur texte
    #    (separateurs normalises -> "out of stock" matche "Out-of-Stock"), ou bouton
    #    panier declare (in_stock_selector) mais absent de la carte.
    # `outofstock` sur la fiche (WooCommerce li.product), variante modificateur
    # `product-card--out-of-stock` (PrestaShop/ONY TCG), ou element de
    # disponibilite `.stock.out-of-stock`. La classe de la carte est un signal
    # FIABLE et independant de la langue (contrairement au texte "Esaurito"/"Sold
    # out" qui depend de la locale rendue). NB : on ne teste que les classes de la
    # carte elle-meme, pas des descendants, pour ne PAS confondre avec une classe
    # d'icone (ex. PrestaShop "material-icons out-of-stock" presente sur chaque
    # carte) -> pour ces icones descendantes on exige le parent `.stock`.
    if _has_oos_class(classes) or node.select_one(".stock.out-of-stock, .stock.outofstock"):
        return "out"
    if _has_marker(text, site.out_of_stock_markers):
        return "out"
    if site.in_stock_selector and not node.select_one(site.in_stock_selector):
        return "out"

    # 2. Precommande COMMANDABLE (on a ecarte les ruptures au-dessus) : label dans
    #    le texte de la carte, ou dans le slug d'URL Shopify (".../precommande-...").
    if _has_marker(text, preorder_markers) or _has_marker(url, preorder_markers):
        return "preorder"

    # 3. Bouton panier present (in_stock_selector) -> stock confirme.
    if site.in_stock_selector:
        return "confirmed"

    # 4. Par defaut : disponible. Si la boutique signale FIABLEMENT ses ruptures
    #    (oos_markers_reliable), l'absence de marqueur vaut "en stock confirme" ;
    #    sinon on reste prudent ("inferred" = non confirme).
    return "confirmed" if site.oos_markers_reliable else "inferred"


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

        stock_status = _stock_status(node, site, url)
        results.append(
            ProductState(
                site=site.name,
                title=title,
                url=url,
                price=scale_price(
                    clean_price(_select_one_text(node, site.selectors.get("price"))),
                    site.price_multiplier,
                ),
                available=stock_status != "out",
                stock_status=stock_status,
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
    def _append_new_products(
        self,
        products: list[ProductState],
        seen_urls: set[str],
        html: str,
    ) -> int:
        added = 0
        for product in parse_products(html, self.site):
            if product.url in seen_urls:
                continue
            seen_urls.add(product.url)
            products.append(product)
            added += 1
        return added

    def _collect_synthetic_pages(
        self,
        products: list[ProductState],
        seen_urls: set[str],
        first_page_urls: set[str],
    ) -> None:
        """Probe ?page=N or /page/N/ pages for JS "load more" catalogues."""
        for page in range(2, self.site.max_pages + 1):
            url = page_url(self.site.url, page, self.site)
            if url in first_page_urls:
                continue
            # Pause entre pages : plusieurs fetchs en rafale font throttler certains
            # sites sur les IP datacenter (ex. Poke-Geek renvoie 0 des la 2e requete
            # rapprochee -> pagination coupee). Un petit delai evite le blocage.
            time.sleep(PAGE_FETCH_DELAY)
            try:
                html = self.fetch_html(url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {404, 410}:
                    log.debug("%s : pagination arretee sur page %d (%d)",
                              self.site.name, page, exc.response.status_code)
                    break
                log.warning("%s : page %d ignoree (%s)", self.site.name, page, exc)
                break
            except httpx.HTTPError as exc:
                log.warning("%s : page %d ignoree (%s)", self.site.name, page, exc)
                break

            added = self._append_new_products(products, seen_urls, html)
            if added == 0:
                break

    def collect(self) -> list[ProductState]:
        products: list[ProductState] = []
        seen_urls: set[str] = set()
        first_html = self.fetch_html(self.site.url)
        self._append_new_products(products, seen_urls, first_html)

        linked_urls = pagination_urls(first_html, self.site.url, self.site)
        for url in linked_urls:
            time.sleep(PAGE_FETCH_DELAY)
            self._append_new_products(products, seen_urls, self.fetch_html(url))

        # Certains PrestaShop n'exposent pas de liens <a href> pour les pages
        # suivantes : le bouton "load more" charge pourtant les memes pages via
        # ?page=N. On les sonde prudemment et on s'arrete des qu'elles ne livrent
        # plus de nouveaux produits.
        if not linked_urls:
            self._collect_synthetic_pages(products, seen_urls, {self.site.url})
        return products
