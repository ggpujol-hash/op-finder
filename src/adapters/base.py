"""Interface commune a tous les adapters de site."""
from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod

import httpx

from ..config import SiteConfig
from ..models import ProductState

log = logging.getLogger("adapter")

# Quelques User-Agents realistes. On en choisit UN par adapter (session) plutot
# que de le changer a chaque requete : un vrai navigateur garde le meme UA sur
# toute sa session ; le faire tourner est en fait plus suspect (anti-bot).
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
]

# Codes HTTP qui valent un nouvel essai (limite de debit ou panne transitoire).
_RETRYABLE = {429, 500, 502, 503, 504}


class Adapter(ABC):
    def __init__(self, site: SiteConfig) -> None:
        self.site = site
        # UA stable pour toute la duree de vie de l'adapter.
        self.user_agent = random.choice(USER_AGENTS)

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7,it;q=0.6",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        # NB : on ne fixe PAS Accept-Encoding manuellement. httpx annonce seulement
        # les encodages qu'il sait decoder (gzip/deflate, + brotli/zstd si les libs
        # sont installees). Forcer "br" sans le paquet brotli renvoie du contenu
        # non decode -> HTML illisible -> 0 produit.
        # Referer credible : la home de la boutique (un vrai visiteur y arrive
        # rarement directement sur la page categorie).
        referer = self.site.base_url or self.site.url
        if referer:
            headers["Referer"] = referer
        return headers

    def fetch_html(self, url: str, attempts: int = 2) -> str:
        """Telecharge le HTML avec un retry sur erreurs transitoires (429/5xx,
        timeout, coupure reseau). Les autres erreurs (403, 404...) remontent
        immediatement pour etre journalisees comme echec du check."""
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                with httpx.Client(
                    timeout=20.0, follow_redirects=True, headers=self._headers()
                ) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    return resp.text
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response.status_code == 403:
                    return self._fetch_html_with_browser(url, e)
                if e.response.status_code not in _RETRYABLE or attempt + 1 >= attempts:
                    raise
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                if attempt + 1 >= attempts:
                    raise
            # Backoff court avec jitter avant de reessayer.
            delay = 1.5 * (attempt + 1) + random.uniform(0, 0.5)
            log.warning("%s : nouvel essai dans %.1fs (%s)", self.site.name, delay, last_exc)
            time.sleep(delay)
        # Inatteignable (la boucle leve ou retourne), mais par securite :
        raise last_exc if last_exc else RuntimeError("fetch_html: echec inconnu")

    def _fetch_html_with_browser(self, url: str, original_error: httpx.HTTPStatusError) -> str:
        """Fallback Chromium pour les WAF qui refusent les requetes HTTP simples."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            raise original_error

        log.warning("%s : HTTP 403 sur %s, fallback navigateur", self.site.name, url)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
                context = browser.new_context(
                    user_agent=self.user_agent,
                    locale="fr-FR",
                    viewport={"width": 1366, "height": 900},
                )
                page = context.new_page()
                try:
                    response = page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3500)
                    if response and response.status >= 400:
                        raise original_error
                    return page.content()
                finally:
                    context.close()
                    browser.close()
        except Exception:
            raise original_error

    @abstractmethod
    def collect(self) -> list[ProductState]:
        """Retourne l'etat courant des produits trouves sur le site."""
        raise NotImplementedError
