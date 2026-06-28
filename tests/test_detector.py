import sqlite3
import unittest

from src.db import SCHEMA
from src.detector import detect
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


if __name__ == "__main__":
    unittest.main()
