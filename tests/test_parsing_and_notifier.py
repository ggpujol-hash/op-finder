import sqlite3
import unittest

from src.adapters.generic_html import page_url, pagination_urls, parse_products
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


    def test_clean_price_normalizes_heterogeneous_formats(self) -> None:
        self.assertEqual(clean_price("119,90 EUR"), "119,90 €")
        self.assertEqual(clean_price("119,90 €"), "119,90 €")
        self.assertEqual(clean_price("\xa0119,90\xa0€"), "119,90 €")
        # Prix barre + promo : on garde le montant courant (le dernier).
        self.assertEqual(clean_price("129,90 € 119,90 €"), "119,90 €")
        self.assertEqual(clean_price("à partir de 1 199,90 €"), "1 199,90 €")
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

    def test_last_successful_items_ignores_failed_checks(self) -> None:
        conn = _make_conn()
        db.log_check(conn, "Shop", ok=True, items=12, message="ok")
        db.log_check(conn, "Shop", ok=False, items=0, message="boom")
        self.assertEqual(db.last_successful_items(conn, "Shop"), 12)
        self.assertIsNone(db.last_successful_items(conn, "Autre"))


if __name__ == "__main__":
    unittest.main()
