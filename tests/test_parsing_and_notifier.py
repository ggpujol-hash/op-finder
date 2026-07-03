import sqlite3
import unittest

from src.adapters.generic_html import GenericHtmlAdapter, page_url, pagination_urls, parse_products
from src.config import AppConfig, SiteConfig
from src import db
from src.detector import apply_filters, detect
from src.models import Event, ProductState, clean_price, normalize_product_url
from src.notifier import format_message


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


def _make_cfg(**over) -> AppConfig:
    base = dict(
        telegram_token="", telegram_chat_id="", check_interval=180, check_jitter=45,
        keywords=["one piece", "op-", "display", "booster"],
        hot_keywords=["op-17", "op17", "op 17"],
        exclude_keywords=[], exclude_lang_codes=[], site_keywords={}, sites=[],
    )
    base.update(over)
    return AppConfig(**base)


class ParsingAndNotifierTest(unittest.TestCase):
    def test_product_urls_are_normalized(self) -> None:
        self.assertEqual(
            normalize_product_url("HTTPS://Example.COM/product/op17/?utm_source=x#details"),
            "https://example.com/product/op17",
        )

        a = ProductState("Shop", "OP17", "https://example.com/product/op17?utm=x")
        b = ProductState("Shop", "OP17", "https://EXAMPLE.com/product/op17/#top")
        self.assertEqual(a.key, b.key)

    def test_parse_products_uses_explicit_in_stock_selector(self) -> None:
        site = SiteConfig(
            name="Shop",
            type="generic_html",
            url="https://example.com",
            base_url="https://example.com",
            selectors={
                "item": ".product",
                "title": ".title",
                "link": "a",
                "price": ".price",
            },
            in_stock_selector=".add-to-cart",
        )
        html = """
        <div class="product">
          <a href="/op17?utm_source=x"><span class="title">One Piece OP-17 Display EN</span></a>
          <span class="price">119,90 EUR</span>
          <button class="add-to-cart">Ajouter</button>
        </div>
        <div class="product">
          <a href="/op16"><span class="title">One Piece OP-16 Display EN</span></a>
          <span class="price">109,90 EUR</span>
        </div>
        """

        products = parse_products(html, site)
        self.assertEqual(len(products), 2)
        self.assertEqual(products[0].url, "https://example.com/op17")
        self.assertTrue(products[0].available)
        self.assertEqual(products[0].stock_status, "confirmed")
        self.assertFalse(products[1].available)
        self.assertEqual(products[1].stock_status, "out")

    def test_oos_reliable_with_hyphenated_flag(self) -> None:
        # PrestaShop : flag "Out-of-Stock" (tirets) doit matcher le marqueur
        # "out of stock" (espaces) ; et l'absence de flag = confirme si fiable.
        site = SiteConfig(
            name="Shop", type="generic_html",
            url="https://example.com", base_url="https://example.com",
            selectors={"item": ".product", "title": ".title", "link": "a", "price": ".price"},
            out_of_stock_markers=["out of stock", "esaurito"],
            oos_markers_reliable=True,
        )
        html = """
        <div class="product">
          <a href="/op10"><span class="title">OP-10 Box</span></a><span class="price">149,90 €</span>
        </div>
        <div class="product">
          <a href="/op01"><span class="title">OP-01 Box</span></a><span class="price">149,90 €</span>
          <span class="product-flags">Out-of-Stock</span>
        </div>
        """
        by_url = {p.url: p for p in parse_products(html, site)}
        self.assertEqual(by_url["https://example.com/op10"].stock_status, "confirmed")
        self.assertTrue(by_url["https://example.com/op10"].available)
        self.assertEqual(by_url["https://example.com/op01"].stock_status, "out")

    def test_out_of_stock_beats_preorder_label(self) -> None:
        # Une precommande "Out of stock" (bouton inactif) n'est pas commandable
        # -> rupture. Une precommande sans marqueur de rupture -> precommande.
        site = SiteConfig(
            name="Ony", type="generic_html",
            url="https://example.com", base_url="https://example.com",
            selectors={"item": ".product-card", "title": ".title", "link": "a"},
            out_of_stock_markers=["out of stock", "esaurito"],
        )
        html = """
        <div class="product-card">
          <a href="/st31"><span class="title">Starter Deck ST-31</span></a>
          <span>Pre-order</span> <span>Out of stock</span>
        </div>
        <div class="product-card">
          <a href="/op12"><span class="title">Booster OP-12</span></a>
          <span>Pre-order</span> <span>Available</span>
        </div>
        """
        by_url = {p.url: p for p in parse_products(html, site)}
        self.assertEqual(by_url["https://example.com/st31"].stock_status, "out")
        self.assertFalse(by_url["https://example.com/st31"].available)
        self.assertEqual(by_url["https://example.com/op12"].stock_status, "preorder")
        self.assertTrue(by_url["https://example.com/op12"].available)

    def test_woocommerce_outofstock_class_beats_cart_button(self) -> None:
        # Certains themes WooCommerce affichent un bouton panier meme sur les
        # produits epuises -> la classe `outofstock` doit faire autorite.
        site = SiteConfig(
            name="Woo", type="generic_html",
            url="https://example.com", base_url="https://example.com",
            selectors={"item": "li.product", "title": ".title", "link": "a"},
            in_stock_selector="a.add_to_cart_button",
        )
        html = """
        <li class="product outofstock">
          <a href="/eb02"><span class="title">EB-02 Anime 25th</span></a>
          <a class="add_to_cart_button" href="?add-to-cart=1">Add to cart</a>
        </li>
        <li class="product instock">
          <a href="/op10"><span class="title">OP-10 Box</span></a>
          <a class="add_to_cart_button" href="?add-to-cart=2">Add to cart</a>
        </li>
        """
        by_url = {p.url: p for p in parse_products(html, site)}
        self.assertEqual(by_url["https://example.com/eb02"].stock_status, "out")
        self.assertFalse(by_url["https://example.com/eb02"].available)
        self.assertEqual(by_url["https://example.com/op10"].stock_status, "confirmed")

    def test_preorder_detected_from_url_slug(self) -> None:
        # Cas Shopify : la carte ne porte aucun marqueur, mais le slug d'URL trahit
        # la precommande -> doit etre classe "preorder", pas "inferred".
        site = SiteConfig(
            name="Shop", type="generic_html",
            url="https://example.com", base_url="https://example.com",
            selectors={"item": ".product", "title": ".title", "link": "a", "price": ".price"},
            out_of_stock_markers=["épuisé", "rupture"],
        )
        html = """
        <div class="product">
          <a href="/products/precommande-display-op16-anglais"><span class="title">Display OP16 Anglais</span></a>
          <span class="price">159,00 €</span>
        </div>
        <div class="product">
          <a href="/products/precommande-display-op17-anglais"><span class="title">Display OP17 Anglais</span></a>
          <span class="price">159,00 €</span>
          <span>Épuisé</span>
        </div>
        """
        products = parse_products(html, site)
        by_url = {p.url: p for p in products}
        op16 = by_url["https://example.com/products/precommande-display-op16-anglais"]
        op17 = by_url["https://example.com/products/precommande-display-op17-anglais"]
        self.assertEqual(op16.stock_status, "preorder")
        self.assertTrue(op16.available)
        # Une preco epuisee reste "out" (rupture prioritaire sur le slug).
        self.assertEqual(op17.stock_status, "out")
        self.assertFalse(op17.available)

    def test_parse_products_detects_preorders(self) -> None:
        site = SiteConfig(
            name="Shop",
            type="generic_html",
            url="https://example.com",
            base_url="https://example.com",
            selectors={
                "item": ".product",
                "title": ".title",
                "link": "a",
                "price": ".price",
            },
        )
        html = """
        <div class="product">
          <a href="/op17"><span class="title">One Piece OP-17 Display EN</span></a>
          <span class="price">119,90 EUR</span>
          <span>Disponible en précommande</span>
        </div>
        """

        products = parse_products(html, site)
        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)
        self.assertEqual(products[0].stock_status, "preorder")

    def test_telegram_message_escapes_external_content(self) -> None:
        event = Event(
            kind="new",
            state=ProductState(
                site="Shop <x>",
                title="One Piece <Display> & OP-17",
                url="https://example.com/product?x=1&y=2",
                price="119 < 120",
            ),
            detail="Alerte & check",
        )

        message = format_message(event)
        self.assertIn("Shop &lt;x&gt;", message)
        self.assertIn("One Piece &lt;Display&gt; &amp; OP-17", message)
        self.assertIn("119 &lt; 120", message)
        self.assertIn("Alerte &amp; check", message)
        self.assertIn('href="https://example.com/product?x=1&amp;y=2"', message)

    def test_page_url_supports_query_and_path_pagination(self) -> None:
        query_site = SiteConfig(
            name="Shop",
            type="generic_html",
            url="https://example.com/collection?sort=price",
            page_param="page",
        )
        path_site = SiteConfig(
            name="Shop",
            type="generic_html",
            url="https://example.com/category/",
            page_style="path",
        )

        self.assertEqual(
            page_url(query_site.url, 2, query_site),
            "https://example.com/collection?sort=price&page=2",
        )
        self.assertEqual(
            page_url(path_site.url, 3, path_site),
            "https://example.com/category/page/3/",
        )

    def test_pagination_urls_are_discovered_from_links(self) -> None:
        site = SiteConfig(
            name="Shop",
            type="generic_html",
            url="https://example.com/collection",
            max_pages=3,
        )
        html = """
        <a href="/collection?page=2">2</a>
        <a href="/collection?page=3">3</a>
        <a href="/collection?page=4">4</a>
        <a href="/other">Other</a>
        """

        self.assertEqual(
            pagination_urls(html, site.url, site),
            [
                "https://example.com/collection?page=2",
                "https://example.com/collection?page=3",
            ],
        )

    def test_generic_html_probes_query_pages_when_no_pagination_links(self) -> None:
        site = SiteConfig(
            name="Shop",
            type="generic_html",
            url="https://example.com/collection",
            base_url="https://example.com",
            max_pages=4,
            selectors={
                "item": ".product",
                "title": ".title",
                "link": "a",
                "price": ".price",
            },
        )
        pages = {
            "https://example.com/collection": """
                <div class="product"><a href="/p1"><span class="title">OP-01 Display</span></a></div>
            """,
            "https://example.com/collection?page=2": """
                <div class="product"><a href="/p2"><span class="title">OP-02 Display</span></a></div>
            """,
            "https://example.com/collection?page=3": """
                <div class="product"><a href="/p2"><span class="title">OP-02 Display</span></a></div>
            """,
        }
        fetched_urls: list[str] = []

        class FakeAdapter(GenericHtmlAdapter):
            def fetch_html(self, url: str, attempts: int = 2) -> str:
                fetched_urls.append(url)
                return pages[url]

        from unittest.mock import patch
        with patch("src.adapters.generic_html.time.sleep"):
            products = FakeAdapter(site).collect()

        self.assertEqual([p.url for p in products], [
            "https://example.com/p1",
            "https://example.com/p2",
        ])
        self.assertEqual(fetched_urls, [
            "https://example.com/collection",
            "https://example.com/collection?page=2",
            "https://example.com/collection?page=3",
        ])

    def test_unblock_routes_to_flaresolverr_when_env_set(self) -> None:
        from unittest.mock import patch
        site = SiteConfig(
            name="CF", type="generic_html", url="https://cf.example",
            base_url="https://cf.example", unblock=True,
            selectors={"item": ".product", "title": ".title", "link": "a"},
        )
        ad = GenericHtmlAdapter(site)
        with patch.dict("os.environ", {"FLARESOLVERR_URL": "http://localhost:8191/v1"}), \
             patch.object(ad, "_fetch_via_flaresolverr", return_value="<html>via-fs</html>") as m:
            out = ad.fetch_html("https://cf.example/page")
        m.assert_called_once()
        self.assertEqual(out, "<html>via-fs</html>")

    def test_flaresolverr_parses_solution_and_flags_block(self) -> None:
        import httpx
        from unittest.mock import MagicMock, patch
        site = SiteConfig(name="CF", type="generic_html", url="https://cf.example",
                          base_url="https://cf.example", unblock=True, selectors={"item": ".p"})
        ad = GenericHtmlAdapter(site)

        ok = MagicMock()
        ok.json.return_value = {"status": "ok", "solution": {"status": 200, "response": "<html>OK</html>"}}
        with patch("src.adapters.base.httpx.post", return_value=ok):
            self.assertEqual(
                ad._fetch_via_flaresolverr("https://cf.example", "http://x/v1"), "<html>OK</html>"
            )
        # Cloudflare refuse encore (403) -> erreur HTTP claire.
        blocked = MagicMock()
        blocked.json.return_value = {"status": "ok", "solution": {"status": 403, "response": "x"}}
        with patch("src.adapters.base.httpx.post", return_value=blocked):
            with self.assertRaises(httpx.HTTPStatusError):
                ad._fetch_via_flaresolverr("https://cf.example", "http://x/v1")

    def test_clean_price_normalizes_heterogeneous_formats(self) -> None:
        self.assertEqual(clean_price("119,90 EUR"), "119,90 €")
        self.assertEqual(clean_price("119,90 €"), "119,90 €")
        self.assertEqual(clean_price("\xa0119,90\xa0€"), "119,90 €")
        # Prix barre + promo : on garde le montant courant (le dernier).
        self.assertEqual(clean_price("129,90 € 119,90 €"), "119,90 €")
        self.assertEqual(clean_price("à partir de 1 199,90 €"), "1 199,90 €")
        # Separateurs de milliers exotiques (fine insecable U+202F / U+2009) :
        # ne doivent PAS tronquer le montant ("2 495,00" -> "495,00").
        self.assertEqual(clean_price("2 495,00 €"), "2 495,00 €")
        self.assertEqual(clean_price("2 495,00€"), "2 495,00 €")
        self.assertEqual(clean_price("2\xa0495,00 €"), "2 495,00 €")
        self.assertIsNone(clean_price(None))

    def test_keywords_keep_displays_without_one_piece_in_title(self) -> None:
        cfg = _make_cfg()
        states = [
            ProductState("Shop", "Display Pilier du Monde OP-09", "https://e.com/op09"),
            ProductState("Shop", "Booster Box EB-02", "https://e.com/eb02"),
            ProductState("Shop", "Figurine Naruto Collector", "https://e.com/naruto"),
        ]
        kept = {p.title for p in apply_filters(states, cfg)}
        self.assertIn("Display Pilier du Monde OP-09", kept)
        self.assertIn("Booster Box EB-02", kept)
        self.assertNotIn("Figurine Naruto Collector", kept)

    def test_site_default_lang_excludes_untagged_products(self) -> None:
        # Un shop declare FR : on ne garde que les produits explicitement tagues EN.
        cfg = _make_cfg(
            exclude_lang_codes=["fr", "jp"],
            include_lang_codes=["en", "eng"],
            site_lang={"ShopFR": "fr"},
        )
        eng = ProductState("ShopFR", "OP-09 Display ENG", "https://e.com/eng")
        untagged = ProductState("ShopFR", "OP-09 Display", "https://e.com/untagged")
        # Sans lang configure, comportement inchange (untagged garde).
        other = ProductState("OtherShop", "OP-09 Display", "https://e.com/other")
        kept = {p.url for p in apply_filters([eng, untagged, other], cfg)}
        self.assertEqual(kept, {"https://e.com/eng", "https://e.com/other"})

    def test_preorder_becoming_confirmed_emits_restock(self) -> None:
        conn = _make_conn()
        url = "https://e.com/op17"
        detect(conn, [ProductState("Shop", "OP17 Display", url,
                                   price="119,90 €", stock_status="preorder")])
        events = detect(conn, [ProductState("Shop", "OP17 Display", url,
                                            price="119,90 €", stock_status="confirmed")])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "restock")
        self.assertIn("desormais disponible", events[0].detail)

    def test_no_phantom_price_change_on_reformatting(self) -> None:
        conn = _make_conn()
        url = "https://e.com/op17"
        detect(conn, [ProductState("Shop", "OP17 Display", url,
                                   price="119,90 EUR", stock_status="confirmed")])
        # Meme prix, format different (devise + espace insecable) -> pas d'alerte.
        events = detect(conn, [ProductState("Shop", "OP17 Display", url,
                                            price="119,90 €", stock_status="confirmed")])
        self.assertEqual(events, [])
        # Un vrai changement de prix declenche bien une alerte.
        events = detect(conn, [ProductState("Shop", "OP17 Display", url,
                                            price="129,90 €", stock_status="confirmed")])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "price_change")

    def test_reconcile_missing_flips_to_out_after_threshold(self) -> None:
        conn = _make_conn()
        a = ProductState("Shop", "OP17 A", "https://e.com/a", stock_status="confirmed")
        b = ProductState("Shop", "OP17 B", "https://e.com/b", stock_status="confirmed")
        detect(conn, [a, b])

        # b absent : 1er passage -> compteur, pas encore bascule.
        self.assertEqual(db.reconcile_missing(conn, "Shop", {a.key}, threshold=2), 0)
        self.assertEqual(
            conn.execute("SELECT available FROM products WHERE key=?", (b.key,)).fetchone()[0], 1
        )
        # 2e passage -> seuil atteint -> bascule en rupture.
        self.assertEqual(db.reconcile_missing(conn, "Shop", {a.key}, threshold=2), 1)
        self.assertEqual(
            conn.execute("SELECT available, stock_status FROM products WHERE key=?",
                         (b.key,)).fetchone()["stock_status"], "out"
        )
        # b revu disponible -> restock detecte (la transition aurait ete perdue sinon).
        events = detect(conn, [ProductState("Shop", "OP17 B", "https://e.com/b",
                                            stock_status="confirmed")])
        self.assertEqual([e.kind for e in events], ["restock"])

    def test_human_error_messages(self) -> None:
        import httpx
        from src.runner import human_error
        req = httpx.Request("GET", "https://e.com")
        forbidden = httpx.HTTPStatusError(
            "boom", request=req, response=httpx.Response(403, request=req)
        )
        self.assertEqual(human_error(forbidden), "Accès refusé (403) — IP bloquée ?")
        self.assertEqual(human_error(httpx.ConnectError("x")), "Connexion impossible")
        self.assertEqual(human_error(httpx.ReadTimeout("x")), "Délai dépassé")
        self.assertEqual(human_error(ValueError("souci precis")), "souci precis")

    def test_recent_alert_exists_dedup_and_window(self) -> None:
        from datetime import datetime, timezone, timedelta
        conn = _make_conn()
        st = ProductState("Shop", "OP17 Display", "https://e.com/op17")
        db.log_alert(conn, st, "restock", "De retour en stock")
        # Doublon exact dans la fenetre -> True.
        self.assertTrue(db.recent_alert_exists(conn, st.key, "restock", "De retour en stock", 12))
        # Detail ou type different -> pas un doublon.
        self.assertFalse(db.recent_alert_exists(conn, st.key, "restock", "Autre detail", 12))
        self.assertFalse(db.recent_alert_exists(conn, st.key, "price_change", "De retour en stock", 12))
        # Alerte hors fenetre -> ignoree.
        old = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
        conn.execute("UPDATE alerts SET sent_at = ? WHERE key = ?", (old, st.key))
        self.assertFalse(db.recent_alert_exists(conn, st.key, "restock", "De retour en stock", 12))

    def test_new_shop_seeded_silently_then_new_product_alerts(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from src import runner, db as dbmod
        from src.adapters.base import Adapter

        box = {"list": [ProductState("NewShop", "Display OP-12", "https://e.com/op12",
                                     stock_status="confirmed")]}

        class FakeAdapter(Adapter):
            def collect(self):
                return list(box["list"])

        class FakeNotifier:
            def __init__(self):
                self.sent = []

            def send(self, ev):
                self.sent.append(ev)
                return True

        site = SiteConfig(name="NewShop", type="generic_html", url="https://e.com",
                          base_url="https://e.com",
                          selectors={"item": ".p", "title": ".t", "link": "a"})
        cfg = _make_cfg()
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as d:
            with patch.object(dbmod, "DB_PATH", Path(d) / "t.db"), \
                 patch.object(runner, "build_adapter", lambda s: FakeAdapter(s)):
                dbmod.init_db()
                # 1er passage = amorcage silencieux (boutique jamais vue).
                runner.run_site_check(site, cfg, notifier)
                self.assertEqual(notifier.sent, [])
                # 2e passage : un produit reellement nouveau -> alerte "new".
                box["list"].append(ProductState("NewShop", "Display OP-13",
                                                 "https://e.com/op13", stock_status="confirmed"))
                runner.run_site_check(site, cfg, notifier)
                self.assertEqual([e.kind for e in notifier.sent], ["new"])
                self.assertEqual([e.state.title for e in notifier.sent], ["Display OP-13"])

    def test_last_successful_items_ignores_failed_checks(self) -> None:
        conn = _make_conn()
        db.log_check(conn, "Shop", ok=True, items=12, message="ok")
        db.log_check(conn, "Shop", ok=False, items=0, message="boom")
        self.assertEqual(db.last_successful_items(conn, "Shop"), 12)
        self.assertIsNone(db.last_successful_items(conn, "Autre"))


if __name__ == "__main__":
    unittest.main()
