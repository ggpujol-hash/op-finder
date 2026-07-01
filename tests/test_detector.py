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
            site_keywords={},
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

    def test_site_specific_keywords_can_match_short_product_names(self) -> None:
        cfg = AppConfig(
            telegram_token="",
            telegram_chat_id="",
            check_interval=180,
            check_jitter=45,
            keywords=["one piece"],
            hot_keywords=[],
            exclude_keywords=[],
            exclude_lang_codes=[],
            site_keywords={"Carte One Piece": ["op", "display"]},
            sites=[],
        )
        short_title = ProductState(
            site="Carte One Piece",
            title="OP16 Display Box - 24 boosters - ENG",
            url="https://example.test/products/op16-display-box",
        )
        unrelated_short_title = ProductState(
            site="Other Shop",
            title="OP16 Display Box - 24 boosters - ENG",
            url="https://example.test/products/op16-display-box",
        )

        self.assertEqual(apply_filters([short_title, unrelated_short_title], cfg), [short_title])

    def test_exclude_type_drops_single_boosters_sleeves_and_starters(self) -> None:
        cfg = AppConfig(
            telegram_token="",
            telegram_chat_id="",
            check_interval=180,
            check_jitter=45,
            keywords=["one piece"],
            hot_keywords=[],
            exclude_keywords=[],
            exclude_lang_codes=[],
            site_keywords={},
            sites=[],
            exclude_type_terms=["sleeve", "starter deck", "deck de démarrage"],
            booster_unit_markers=["booster", "blister"],
            bulk_markers=["box", "display", "boîte", "boite"],
        )

        def state(title: str) -> ProductState:
            return ProductState(site="Shop", title=title, url="https://x.test/" + title[:5])

        kept = {
            "One Piece OP16 Display FR",            # display -> garde
            "One Piece Booster Box OP17 ENG",       # booster + box -> garde
            "One Piece Boîte de 24 Boosters OP15",  # booster + boite -> garde
            "One Piece OP15 Double Pack",           # ni booster ni starter -> garde
        }
        dropped = {
            "One Piece OP16 - Booster FR",          # booster a l'unite -> exclu
            "One Piece OP10 Booster VO (blister)",  # blister sans lot -> exclu
            "One Piece Sleeves Luffy 60p",          # sleeve -> exclu
            "One Piece Starter Deck ST30",          # starter deck -> exclu
            "One Piece Deck de démarrage ST29",     # starter deck (FR) -> exclu
        }
        states = [state(t) for t in kept | dropped]
        result = {p.title for p in apply_filters(states, cfg)}
        self.assertEqual(result, kept)


if __name__ == "__main__":
    unittest.main()
