"""Boucle de monitoring : execute les checks et declenche les alertes."""
from __future__ import annotations

import logging
import random
import time

from apscheduler.schedulers.background import BackgroundScheduler

from . import db
from .adapters import build_adapter
from .config import AppConfig, SiteConfig
from .detector import apply_filters, detect
from .notifier import TelegramNotifier

log = logging.getLogger("runner")


def run_site_check(site: SiteConfig, cfg: AppConfig, notifier: TelegramNotifier,
                   seed: bool = False) -> int:
    """Execute un check sur un site. Retourne le nombre d'evenements alertes.

    En mode seed=True, on remplit la base sans envoyer d'alerte ni journaliser
    d'evenement (utile au tout premier lancement pour ne pas spammer Telegram
    avec tout le catalogue existant).
    """
    if not site.enabled:
        return 0
    log.info("%s : %s", "Seed" if seed else "Check", site.name)
    conn = db.connect()
    try:
        adapter = build_adapter(site)
        states = adapter.collect()
        states = apply_filters(states, cfg)
        items = len(states)

        # Panne silencieuse : un site qui retournait des produits et tombe a 0
        # (HTTP 200 mais selecteurs casses) ne doit pas rester "vert" a items=0.
        prev_items = db.last_successful_items(conn, site.name)
        if items == 0 and prev_items:
            db.log_check(conn, site.name, ok=False, items=0,
                         message=f"0 produit (precedent : {prev_items}) — selecteurs casses ?")
            conn.commit()
            log.warning("%s : 0 produit alors que %d au dernier check — selecteurs casses ?",
                        site.name, prev_items)
            return -1

        events = detect(conn, states)

        # Marque en rupture les produits disparus du listing depuis plusieurs
        # passages, pour qu'un futur retour declenche bien un restock. Uniquement
        # sur un check fiable (reussi et non vide), jamais en seed.
        if not seed and items:
            flipped = db.reconcile_missing(conn, site.name, {st.key for st in states})
            if flipped:
                log.info("%s : %d produit(s) disparu(s) marque(s) en rupture", site.name, flipped)

        sent = 0
        if not seed:
            for ev in events:
                if notifier.send(ev):
                    sent += 1
                db.log_alert(conn, ev.state, ev.kind, ev.detail)
        db.log_check(conn, site.name, ok=True, items=items,
                     message=("seed" if seed else f"{len(events)} evenement(s)"))
        conn.commit()
        log.info("%s : %d produits, %d evenement(s), %d alerte(s) envoyee(s)",
                 site.name, items, 0 if seed else len(events), sent)
        return len(events)
    except Exception as e:  # noqa: BLE001 — on isole chaque site
        log.error("%s : echec du check — %s", site.name, e)
        db.log_check(conn, site.name, ok=False, items=0, message=str(e))
        conn.commit()
        return -1
    finally:
        conn.close()


def run_once(cfg: AppConfig, notifier: TelegramNotifier, seed: bool = False) -> None:
    active = 0
    failed = 0
    for site in cfg.sites:
        if not site.enabled:
            continue
        active += 1
        if run_site_check(site, cfg, notifier, seed=seed) < 0:
            failed += 1
    if active and failed == active:
        raise RuntimeError("Tous les sites actifs ont echoue pendant ce passage")
    # Nettoie les produits plus vus depuis longtemps (langues exclues, retraits...).
    removed = db.prune_stale(days=2.0)
    if removed:
        log.info("Purge : %d produit(s) perime(s) supprime(s)", removed)


def run_forever(cfg: AppConfig, notifier: TelegramNotifier) -> None:
    db.init_db()
    scheduler = BackgroundScheduler(timezone="UTC")
    for site in cfg.sites:
        if not site.enabled:
            continue
        interval = site.interval_seconds or cfg.check_interval
        jitter = cfg.check_jitter
        # Premier passage decale aleatoirement pour ne pas tout lancer en meme temps.
        scheduler.add_job(
            run_site_check,
            "interval",
            seconds=interval,
            jitter=jitter,
            args=[site, cfg, notifier],
            id=site.name,
            next_run_time=None,
        )
    scheduler.start()
    log.info("Scheduler demarre (%d site(s) actif(s)). Ctrl+C pour arreter.",
             sum(1 for s in cfg.sites if s.enabled))

    # Premier passage immediat (echelonne) pour amorcer la base.
    for site in cfg.sites:
        if site.enabled:
            run_site_check(site, cfg, notifier)
            time.sleep(random.uniform(1, 3))

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        log.info("Arret demande.")
        scheduler.shutdown()
