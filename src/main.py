"""Point d'entree CLI.

Commandes :
    python -m src.main run        # lance le monitoring en continu
    python -m src.main once       # un seul passage sur tous les sites
    python -m src.main seed       # remplit la base sans alerter (1er lancement)
    python -m src.main test       # envoie un message Telegram de test
    python -m src.main probe URL  # inspecte une page pour caler les selecteurs
"""
from __future__ import annotations

import logging
import sys

from . import db
from .config import load_config
from .notifier import TelegramNotifier
from .runner import run_forever, run_once


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx loggue l'URL complete des requetes, ce qui exposerait le token
    # Telegram dans les logs. On le passe en WARNING pour l'eviter.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def cmd_run() -> None:
    cfg = load_config()
    notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)
    run_forever(cfg, notifier)


def cmd_once() -> None:
    cfg = load_config()
    db.init_db()
    notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)
    run_once(cfg, notifier)


def cmd_seed() -> None:
    """Remplit la base sans envoyer d'alerte (a lancer une seule fois au depart)."""
    cfg = load_config()
    db.init_db()
    notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)
    run_once(cfg, notifier, seed=True)
    print("Base initialisee (aucune alerte envoyee).")


def cmd_test() -> None:
    cfg = load_config()
    notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)
    ok = notifier.send_text("✅ OP Finder est connecte. Les alertes arriveront ici.")
    print("Message envoye." if ok else "Echec (verifie TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")


def cmd_probe(url: str) -> None:
    """Aide a trouver item_selector : liste les blocs repetes et les liens produits."""
    import httpx
    from bs4 import BeautifulSoup

    from .adapters.base import USER_AGENTS

    headers = {"User-Agent": USER_AGENTS[0], "Accept-Language": "fr-FR,fr;q=0.9"}
    resp = httpx.get(url, headers=headers, timeout=20.0, follow_redirects=True)
    print(f"HTTP {resp.status_code} — {len(resp.text)} octets\n")
    soup = BeautifulSoup(resp.text, "html.parser")

    # Classes les plus frequentes (candidats item_selector).
    from collections import Counter

    counter: Counter[str] = Counter()
    for el in soup.find_all(True):
        classes = el.get("class")
        if classes:
            counter[f"{el.name}.{'.'.join(classes)}"] += 1

    print("== Blocs repetes (candidats 'item') ==")
    for sel, n in counter.most_common(25):
        if n >= 3:
            print(f"  {n:4d}x  {sel}")

    print("\n== Premiers liens contenant 'product' / 'produit' ==")
    seen = 0
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(t in href.lower() for t in ("product", "produit", "/p/", "article")):
            print(f"  {a.get_text(strip=True)[:60]!r} -> {href}")
            seen += 1
            if seen >= 15:
                break


def main(argv: list[str]) -> int:
    _setup_logging()
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "run":
        cmd_run()
    elif cmd == "once":
        cmd_once()
    elif cmd == "seed":
        cmd_seed()
    elif cmd == "test":
        cmd_test()
    elif cmd == "probe":
        if len(argv) < 3:
            print("Usage: python -m src.main probe <URL>")
            return 1
        cmd_probe(argv[2])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
