import unittest

from src.adapters.generic_html import page_url, pagination_urls, parse_products
from src.config import SiteConfig
from src.models import Event, ProductState, normalize_product_url
from src.notifier import format_message


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
        self.assertFalse(products[1].available)

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


if __name__ == "__main__":
    unittest.main()
