"""Adapter base sur un navigateur headless (Playwright).

Pour les sites qui rendent leur catalogue en JavaScript (Play-In) ou qui
protegent leurs pages contre les requetes simples (Cardmarket / Cloudflare).
On charge la page dans Chromium, on attend que le contenu apparaisse, puis on
reutilise exactement le meme parsing par selecteurs que l'adapter generic_html.
"""
from __future__ import annotations

import logging

from ..models import ProductState
from .base import Adapter
from .generic_html import parse_products

log = logging.getLogger("adapter.playwright")


class PlaywrightHtmlAdapter(Adapter):
    def collect(self) -> list[ProductState]:
        # Import paresseux : Playwright n'est requis que pour ces sites.
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright

        site = self.site
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
                page.goto(site.url, timeout=30000, wait_until="domcontentloaded")

                wait_for = site.wait_for or site.selectors.get("item", "").split(",")[0].strip()
                if wait_for:
                    try:
                        page.wait_for_selector(wait_for, timeout=15000)
                    except PWTimeout:
                        log.warning("%s : selecteur '%s' jamais apparu", site.name, wait_for)

                if site.scroll:
                    self._autoscroll(page)

                if site.wait_ms:
                    page.wait_for_timeout(site.wait_ms)

                html = page.content()
            finally:
                context.close()
                browser.close()

        return parse_products(html, site)

    @staticmethod
    def _autoscroll(page) -> None:
        """Scroll progressif pour declencher le lazy-load des grilles produit."""
        page.evaluate(
            """async () => {
                await new Promise((resolve) => {
                    let total = 0;
                    const step = 600;
                    const timer = setInterval(() => {
                        window.scrollBy(0, step);
                        total += step;
                        if (total >= document.body.scrollHeight) {
                            clearInterval(timer);
                            resolve();
                        }
                    }, 200);
                });
            }"""
        )
