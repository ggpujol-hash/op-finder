import sqlite3
import unittest

from src.db import SCHEMA
from src.config import AppConfig
from src.detector import apply_filters, detect
from src.models import ProductState


class DetectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self) -> None:
        self.conn.close()

    def test_new_product_then_stable_then_restock(self) -> None:
        product = ProductState(
            site="TestShop",
            title="One Piece OP-17 Display EN",
            url="https://example.test/op17",
            price="119,90 EUR",
            available=False,
        )

        self.assertEqual(
            [(event.kind, event.detail) for event in detect(self.conn, [product])],
            [("new", "Reference creee (indispo)")],
        )
        self.assertEqual(detect(self.conn, [product]), [])

        restocked = ProductState(
            site=product.site,
            title=product.title,
            url=product.url,
            price=product.price,
            available=True,
        )
        self.assertEqual(
            [(event.kind, event.detail) for event in detect(self.conn, [restocked])],
            [("restock", "De retour en stock")],
        )

    def test_language_filter_reads_slug_without_locale_false_positive(self) -> None:
        cfg = AppConfig(
            telegram_token="",
            telegram_chat_id="",
            check_interval=180,
            check_jitter=45,
            keywords=["one piece"],
            hot_keywords=[],
            exclude_keywords=[],
            exclude_lang_codes=["fr", "jap"],
            sites=[],
        )
        english = ProductState(
            site="Shop",
            title="One Piece Booster Box ENG",
            url="https://example.test/fr/one-piece-booster-box-eng",
        )
        japanese = ProductState(
            site="Shop",
            title="One Piece Booster Box...",
            url="https://example.test/en/one-piece-booster-box-sealed-jap",
        )

        self.assertEqual(apply_filters([english, japanese], cfg), [english])


if __name__ == "__main__":
    unittest.main()
