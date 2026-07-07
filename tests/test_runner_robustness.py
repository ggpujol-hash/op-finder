"""Tests des garde-fous de robustesse : retry/echec d'envoi, alertes de sante,
garde anti-bascule massive, cadence par site, checkpoint WAL."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import db, runner
from src.config import AppConfig, SiteConfig
from src.models import ProductState


def _cfg() -> AppConfig:
    return AppConfig(
        telegram_token="", telegram_chat_id="", check_interval=180, check_jitter=45,
        keywords=["one piece", "display", "booster"],
        hot_keywords=[], exclude_keywords=[], exclude_lang_codes=[],
        site_keywords={}, sites=[],
    )


def _site(name: str = "TestShop", interval: int = 180) -> SiteConfig:
    return SiteConfig(name=name, type="generic_html", url="https://x.test/one-piece",
                      base_url="https://x.test", interval_seconds=interval)


def _prod(url: str, available: bool = True, title: str = "One Piece OP-17 Display") -> ProductState:
    return ProductState(site="TestShop", title=title, url=url, available=available)


class FakeAdapter:
    """Adapter injecte a la place du vrai : renvoie une liste programmee, ou leve."""

    def __init__(self, holder: "FakeState") -> None:
        self.holder = holder

    def collect(self):
        if self.holder.raises:
            raise self.holder.raises
        return list(self.holder.states)


class FakeState:
    def __init__(self) -> None:
        self.states: list[ProductState] = []
        self.raises: Exception | None = None


class FakeNotifier:
    """Notifier de test : `enabled`, `send` configurable, `send_text` enregistre."""

    def __init__(self, enabled: bool = True, send_ok: bool = True) -> None:
        self.enabled = enabled
        self.send_ok = send_ok
        self.sent: list = []
        self.texts: list[str] = []

    def send(self, ev) -> bool:
        if self.send_ok:
            self.sent.append(ev)
            return True
        return False

    def send_text(self, text: str) -> bool:
        self.texts.append(text)
        return True


class RunnerRobustnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name) / "test.db"
        db.init_db()
        self.holder = FakeState()
        self._patch = mock.patch.object(runner, "build_adapter",
                                        lambda site: FakeAdapter(self.holder))
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        db.DB_PATH = self._orig_db_path
        self._tmp.cleanup()

    def _run(self, notifier: FakeNotifier, site: SiteConfig | None = None) -> int:
        return runner.run_site_check(site or _site(), _cfg(), notifier)

    def _alerts(self) -> list[sqlite3.Row]:
        with db.connect() as conn:
            return conn.execute("SELECT * FROM alerts").fetchall()

    def _bring_to_restock(self, notifier: FakeNotifier) -> None:
        """Sequence amenant un produit a generer un evenement 'restock' non-quiet.

        A (seed quiet) -> B (produit en rupture, prev_items>0) -> C (dispo)."""
        url = "https://x.test/op17"
        self.holder.states = [_prod(url, available=True)]
        self._run(notifier)                       # A : seed silencieux
        self.holder.states = [_prod(url, available=False)]
        self._run(notifier)                       # B : sell-out (pas d'alerte)
        self.holder.states = [_prod(url, available=True)]
        self._run(notifier)                       # C : restock -> alerte

    def test_failed_send_is_not_logged_so_it_can_be_retried(self) -> None:
        notifier = FakeNotifier(enabled=True, send_ok=False)
        self._bring_to_restock(notifier)
        # L'envoi a echoue : AUCUNE alerte ne doit etre journalisee, sinon
        # l'anti-doublon la bloquerait et le restock serait definitivement perdu.
        self.assertEqual(self._alerts(), [])

    def test_successful_send_is_logged(self) -> None:
        notifier = FakeNotifier(enabled=True, send_ok=True)
        self._bring_to_restock(notifier)
        rows = self._alerts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "restock")

    def test_disabled_notifier_still_logs_to_avoid_reprocessing(self) -> None:
        notifier = FakeNotifier(enabled=False, send_ok=False)
        self._bring_to_restock(notifier)
        # Pas de canal -> rien a re-tenter : on journalise pour ne pas re-traiter.
        self.assertEqual(len(self._alerts()), 1)

    def test_health_alert_only_after_sustained_outage(self) -> None:
        from src.runner import HEALTH_DOWN_AFTER
        notifier = FakeNotifier()
        url = "https://x.test/op17"
        self.holder.states = [_prod(url)]
        self._run(notifier)                       # 1er check OK -> muet
        self.assertEqual(notifier.texts, [])

        import httpx
        self.holder.raises = httpx.ConnectError("boom")
        # Les premiers echecs (sous le seuil) restent silencieux : un blip revient seul.
        for _ in range(HEALTH_DOWN_AFTER - 1):
            self._run(notifier)
            self.assertEqual(notifier.texts, [], "un blip ne doit pas alerter")
        # Au franchissement du seuil, une (seule) alerte de panne part.
        self._run(notifier)
        self.assertEqual(len(notifier.texts), 1)
        self.assertIn("hors ligne", notifier.texts[0])
        # Un echec supplementaire ne re-alerte pas (pas de spam de rappel).
        self._run(notifier)
        self.assertEqual(len(notifier.texts), 1)

        self.holder.raises = None
        self.holder.states = [_prod(url)]
        self._run(notifier)                       # retabli apres panne annoncee -> alerte
        self.assertEqual(len(notifier.texts), 2)
        self.assertIn("operationnel", notifier.texts[1])

    def test_health_blip_stays_silent(self) -> None:
        """Un site qui blippe hors-ligne 1-2 checks puis revient ne genere aucune
        alerte (ni panne ni retour) — c'etait la source du spam."""
        from src.runner import HEALTH_DOWN_AFTER
        notifier = FakeNotifier()
        url = "https://x.test/op17"
        self.holder.states = [_prod(url)]
        self._run(notifier)                       # OK
        import httpx
        self.holder.raises = httpx.ConnectError("boom")
        for _ in range(HEALTH_DOWN_AFTER - 1):     # blip sous le seuil
            self._run(notifier)
        self.holder.raises = None                  # revient seul
        self.holder.states = [_prod(url)]
        self._run(notifier)
        self.assertEqual(notifier.texts, [])

    def test_mass_oos_regression_is_ignored(self) -> None:
        notifier = FakeNotifier()
        urls = [f"https://x.test/op{i}" for i in range(6)]
        self.holder.states = [_prod(u, available=True) for u in urls]
        self._run(notifier)                       # seed : 6 dispo
        self._run(notifier)                       # confirme prev_items=6

        # Regression de selecteur simulee : les 6 passent en rupture d'un coup.
        self.holder.states = [_prod(u, available=False) for u in urls]
        rc = self._run(notifier)
        self.assertEqual(rc, -1)                   # check ignore

        # La base ne doit PAS avoir bascule les produits en rupture (sinon tempete
        # de faux restocks au retour a la normale).
        with db.connect() as conn:
            avail = [r["available"] for r in conn.execute("SELECT available FROM products")]
            self.assertTrue(all(avail))
            # Le check est marque en echec (sante) -> visible sur le dashboard.
            self.assertIs(db.last_check_ok(conn, "TestShop"), False)

    def test_mass_oos_guard_allows_genuine_partial_sellout(self) -> None:
        notifier = FakeNotifier()
        urls = [f"https://x.test/op{i}" for i in range(6)]
        self.holder.states = [_prod(u, available=True) for u in urls]
        self._run(notifier)
        self._run(notifier)
        # Seulement 2/6 partent en rupture -> sous le seuil -> check accepte.
        self.holder.states = (
            [_prod(u, available=False) for u in urls[:2]]
            + [_prod(u, available=True) for u in urls[2:]]
        )
        rc = self._run(notifier)
        self.assertGreaterEqual(rc, 0)
        with db.connect() as conn:
            n_out = conn.execute("SELECT COUNT(*) c FROM products WHERE available=0").fetchone()["c"]
        self.assertEqual(n_out, 2)


class DbHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)

    def tearDown(self) -> None:
        self.conn.close()

    def test_last_check_ok_transitions(self) -> None:
        self.assertIsNone(db.last_check_ok(self.conn, "S"))
        db.log_check(self.conn, "S", ok=True, items=5)
        self.assertIs(db.last_check_ok(self.conn, "S"), True)
        db.log_check(self.conn, "S", ok=False, items=0, message="boom")
        self.assertIs(db.last_check_ok(self.conn, "S"), False)

    def test_consecutive_failures(self) -> None:
        self.assertEqual(db.consecutive_failures(self.conn, "S"), 0)
        db.log_check(self.conn, "S", ok=True, items=5)
        self.assertEqual(db.consecutive_failures(self.conn, "S"), 0)
        db.log_check(self.conn, "S", ok=False, items=0)
        db.log_check(self.conn, "S", ok=False, items=0)
        self.assertEqual(db.consecutive_failures(self.conn, "S"), 2)
        db.log_check(self.conn, "S", ok=True, items=5)  # remet le compteur a zero
        self.assertEqual(db.consecutive_failures(self.conn, "S"), 0)

    def test_reconcile_threshold_is_two(self) -> None:
        st = ProductState(site="S", title="OP17 Display", url="https://x.test/op17")
        from src.detector import detect
        detect(self.conn, [st])                    # produit vu, dispo
        # 1re absence : pas encore bascule (seuil 2).
        self.assertEqual(db.reconcile_missing(self.conn, "S", set()), 0)
        # 2e absence : bascule en rupture.
        self.assertEqual(db.reconcile_missing(self.conn, "S", set()), 1)
        row = db.get_product(self.conn, st.key)
        self.assertEqual(row["available"], 0)

    def test_cooldown_shorter_for_restock(self) -> None:
        self.assertEqual(runner._cooldown_hours("restock"), runner.RESTOCK_COOLDOWN_HOURS)
        self.assertEqual(runner._cooldown_hours("new"), runner.ALERT_COOLDOWN_HOURS)
        self.assertLess(runner.RESTOCK_COOLDOWN_HOURS, runner.ALERT_COOLDOWN_HOURS)


class NotifierRetryTest(unittest.TestCase):
    def _notifier(self):
        from src.notifier import TelegramNotifier
        return TelegramNotifier("tok", "chat")

    def test_send_text_retries_then_fails(self) -> None:
        import httpx
        notifier = self._notifier()
        with mock.patch("src.notifier.httpx.post",
                        side_effect=httpx.ConnectError("down")) as post, \
             mock.patch("src.notifier.time.sleep"):
            ok = notifier.send_text("hello")
        self.assertFalse(ok)
        from src.notifier import SEND_ATTEMPTS
        self.assertEqual(post.call_count, SEND_ATTEMPTS)

    def test_send_text_gives_up_immediately_on_4xx(self) -> None:
        import httpx
        notifier = self._notifier()
        req = httpx.Request("POST", "https://api.telegram.org")
        resp = httpx.Response(400, request=req)
        err = httpx.HTTPStatusError("bad", request=req, response=resp)
        with mock.patch("src.notifier.httpx.post") as post, \
             mock.patch("src.notifier.time.sleep"):
            post.return_value = mock.Mock(raise_for_status=mock.Mock(side_effect=err))
            ok = notifier.send_text("hello")
        self.assertFalse(ok)
        self.assertEqual(post.call_count, 1)       # 400 : pas de retry


if __name__ == "__main__":
    unittest.main()
