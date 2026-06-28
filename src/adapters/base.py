"""Interface commune a tous les adapters de site."""
from __future__ import annotations

import random
from abc import ABC, abstractmethod

import httpx

from ..config import SiteConfig
from ..models import ProductState

# Quelques User-Agents realistes, tournes a chaque requete.
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
]


class Adapter(ABC):
    def __init__(self, site: SiteConfig) -> None:
        self.site = site

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    def fetch_html(self, url: str) -> str:
        with httpx.Client(timeout=20.0, follow_redirects=True, headers=self._headers()) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text

    @abstractmethod
    def collect(self) -> list[ProductState]:
        """Retourne l'etat courant des produits trouves sur le site."""
        raise NotImplementedError
