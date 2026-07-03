"""Boucle de monitoring : execute les checks et declenche les alertes."""
from __future__ import annotations

import logging
import random
import time

import httpx
from apscheduler.schedulers.background import BackgroundScheduler

from . import db
from .adapters import build_adapter
from .config import AppConfig, SiteConfig
from .detector import apply_filters, detect
from .notifier import TelegramNotifier

log = logging.getLogger("runner")

# Anti-spam : on ne renvoie pas une alerte identique avant ce delai (un produit
# qui flappe dispo<->rupture sur un scrape intermittent sinon spamme).
ALERT_COOLDOWN_HOURS = 12.0
# reconcile : on ne marque des produits « disparus » que si le scrape a ramene
# au moins cette fraction du volume habituel (sinon un scrape partiel pénalise
# tout le catalogue et provoque de faux restocks au passage suivant).
RECONCILE_MIN_RATIO = 0.6

_HTTP_LABELS = {
    403: "Accès refusé (403) — IP bloquée ?",
    404: "Page introuvable (404)",
    410: "Page supprimée (410)",
    429: "Trop de requêtes (429)",
    500: "Erreur serveur (500)",
    502: "Passerelle invalide (502)",
    503: "Service indisponible (503)",
}


def human_error(exc: Exception) -> str:
    """Transforme une exception technique en message court et lisible pour le
    panneau santé (au lieu d'un dump httpx avec une URL MDN)."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return _HTTP_LABELS.get(code, f"Erreur HTTP {code}")
    if isinstance(exc, httpx.TimeoutException):
        return "Délai dépassé"
    if isinstance(exc, httpx.ConnectError):
        return "Connexion impossible"
    if isinstance(exc, httpx.HTTPError):
        return "Erreur réseau"
    msg = str(exc).strip() or exc.__class__.__name__
    return msg if len(msg) <= 80 else msg[:79] + "…"


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
        states = apply_filters(adapter.collect(), cfg)
        items = len(states)

        # Panne silencieuse : un site qui retournait des produits et tombe a 0
        # (HTTP 200 mais selecteurs casses) ne doit pas rester "vert" a items=0.
        prev_items = db.last_successful_items(conn, site.name)
        # Throttle passager : certains sites renvoient 0 produit quand l'IP CI est
        # sollicitee trop vite (page vide, pas d'erreur HTTP). Avant de crier a la
        # panne, on retente une fois apres une courte pause.
        if items == 0 and prev_items:
            time.sleep(5)
            states = apply_filters(adapter.collect(), cfg)
            items = len(states)
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
        # sur un check fiable (reussi et non vide), jamais en seed. On exige en
        # plus un scrape « complet » (proche du volume habituel) : un scrape
        # partiel (throttling CI...) ne doit pas marquer tout le monde disparu.
        if not seed and items:
            complete = prev_items is None or items >= prev_items * RECONCILE_MIN_RATIO
            if complete:
                flipped = db.reconcile_missing(conn, site.name, {st.key for st in states})
                if flipped:
                    log.info("%s : %d produit(s) disparu(s) marque(s) en rupture", site.name, flipped)
            else:
                log.info("%s : scrape partiel (%d, habituel ~%d) — reconcile saute",
                         site.name, items, prev_items)

        # Premier passage reussi pour CE site (ex. boutique nouvellement ajoutee
        # apres le seed initial) : on peuple sans alerter, comme un seed, pour ne
        # pas envoyer tout son catalogue existant en "NOUVEAU" d'un coup. Les
        # produits reellement nouveaux aux passages suivants alerteront normalement.
        site_seed = prev_items is None
        quiet = seed or site_seed

        sent = 0
        if not quiet:
            for ev in events:
                # Anti-spam : on saute une alerte deja envoyee recemment (flapping).
                if db.recent_alert_exists(conn, ev.state.key, ev.kind, ev.detail, ALERT_COOLDOWN_HOURS):
                    log.info("%s : alerte doublon ignoree (< %gh) — %s",
                             site.name, ALERT_COOLDOWN_HOURS, ev.state.title)
                    continue
                if notifier.send(ev):
                    sent += 1
                db.log_alert(conn, ev.state, ev.kind, ev.detail)
        db.log_check(conn, site.name, ok=True, items=items,
                     message=("seed" if quiet else f"{len(events)} evenement(s)"))
        conn.commit()
        log.info("%s : %d produits, %d evenement(s), %d alerte(s) envoyee(s)%s",
                 site.name, items, 0 if quiet else len(events), sent,
                 " [amorcage]" if site_seed and not seed else "")
        return len(events)
    except Exception as e:  # noqa: BLE001 — on isole chaque site
        log.error("%s : echec du check — %s", site.name, e)
        db.log_check(conn, site.name, ok=False, items=0, message=human_error(e))
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
