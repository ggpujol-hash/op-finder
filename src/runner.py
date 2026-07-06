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
# Cooldown plus court pour les RESTOCK : un display peut restocker, partir en
# quelques minutes, puis restocker a nouveau — ces 2e/3e restocks sont reels et
# ne doivent pas etre etouffes 12 h comme un doublon. On garde une fenetre courte
# juste anti-flapping.
RESTOCK_COOLDOWN_HOURS = 3.0
# reconcile : on ne marque des produits « disparus » que si le scrape a ramene
# au moins cette fraction du volume habituel (sinon un scrape partiel pénalise
# tout le catalogue et provoque de faux restocks au passage suivant).
RECONCILE_MIN_RATIO = 0.6
# Garde-fou anti-bascule massive : si une part >= MASS_OOS_RATIO des produits
# jusque-la disponibles passe d'un coup en rupture (et qu'il y en a au moins
# MASS_OOS_MIN), on suspecte une regression de selecteur (ex. classe du bouton
# panier changee) plutot qu'un vrai sell-out global -> check ignore.
MASS_OOS_RATIO = 0.8
MASS_OOS_MIN = 5


def _cooldown_hours(kind: str) -> float:
    return RESTOCK_COOLDOWN_HOURS if kind == "restock" else ALERT_COOLDOWN_HOURS


def _mass_oos_regression(prev_avail: dict[str, bool], states) -> bool:
    """Vrai si ce check fait basculer en masse dispo->rupture (selecteur casse ?).

    On compte, parmi les produits que la base avait comme disponibles ET qu'on
    revoit ce passage, ceux qui apparaissent soudain en rupture. Au-dela du seuil,
    c'est presque surement un signal de stock casse (ex. `in_stock_selector` dont
    la classe a change rend TOUT « out »), pas un vrai sell-out simultane."""
    seen_prev_avail = 0
    flipped = 0
    for st in states:
        if prev_avail.get(st.key):
            seen_prev_avail += 1
            if not st.available:
                flipped += 1
    return seen_prev_avail >= MASS_OOS_MIN and flipped >= seen_prev_avail * MASS_OOS_RATIO


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
    # Statut du dernier check AVANT ce passage : sert a detecter une transition de
    # sante (sain <-> casse) pour pousser une alerte. On l'enregistre au fil des
    # points de sortie via _record_check().
    prev_ok = db.last_check_ok(conn, site.name)

    def _record_check(ok: bool, items: int, message: str) -> None:
        db.log_check(conn, site.name, ok=ok, items=items, message=message)
        # Alerte de sante uniquement sur TRANSITION, hors seed, pour ne pas spammer.
        if seed or prev_ok is None:
            return
        if prev_ok and not ok:
            notifier.send_text(f"⚠️ <b>{site.name}</b> ne repond plus — {message}")
        elif not prev_ok and ok:
            notifier.send_text(f"✅ <b>{site.name}</b> de nouveau operationnel ({items} produits)")

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
            _record_check(False, 0,
                          f"0 produit (precedent : {prev_items}) — selecteurs casses ?")
            conn.commit()
            log.warning("%s : 0 produit alors que %d au dernier check — selecteurs casses ?",
                        site.name, prev_items)
            return -1

        # Garde-fou anti-bascule massive : une regression de selecteur de stock
        # (ex. classe du bouton panier changee) ferait passer TOUT le catalogue en
        # rupture, puis exploserait en faux restocks au retour. On ignore le check
        # sans toucher a la base (les produits restent dispo -> pas de faux 'out').
        if not seed and _mass_oos_regression(db.available_map(conn, site.name), states):
            _record_check(False, items,
                          "bascule massive en rupture — signal de stock casse ?")
            conn.commit()
            log.warning("%s : %d produits mais bascule massive dispo->rupture — check ignore",
                        site.name, items)
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
        # `not prev_items` couvre None (jamais vu) ET 0 : une boutique amorcee a
        # vide (selecteurs a caler, blocage CI...) qui se met soudain a renvoyer son
        # catalogue ne doit PAS le deverser en alertes -> on la (re)seed en silence.
        site_seed = not prev_items
        quiet = seed or site_seed

        sent = 0
        if not quiet:
            for ev in events:
                # Anti-spam : on saute une alerte deja envoyee recemment (flapping).
                # Fenetre plus courte pour les restocks (vrai cycle stock rapide).
                cooldown = _cooldown_hours(ev.kind)
                if db.recent_alert_exists(conn, ev.state.key, ev.kind, ev.detail, cooldown):
                    log.info("%s : alerte doublon ignoree (< %gh) — %s",
                             site.name, cooldown, ev.state.title)
                    continue
                # On ne journalise l'alerte QUE si Telegram l'a acceptee : sinon
                # l'anti-doublon la bloquerait et le restock serait perdu. Un echec
                # d'envoi (reseau, 429) laisse l'evenement non journalise -> il sera
                # re-tente au passage suivant. En mode desactive (pas de canal), on
                # journalise quand meme : il n'y a rien a re-tenter.
                if notifier.send(ev):
                    sent += 1
                    db.log_alert(conn, ev.state, ev.kind, ev.detail)
                elif not notifier.enabled:
                    db.log_alert(conn, ev.state, ev.kind, ev.detail)
                else:
                    log.warning("%s : envoi echoue, alerte re-tentee au prochain passage — %s",
                                site.name, ev.state.title)
        _record_check(True, items, ("seed" if quiet else f"{len(events)} evenement(s)"))
        conn.commit()
        log.info("%s : %d produits, %d evenement(s), %d alerte(s) envoyee(s)%s",
                 site.name, items, 0 if quiet else len(events), sent,
                 " [amorcage]" if site_seed and not seed else "")
        return len(events)
    except Exception as e:  # noqa: BLE001 — on isole chaque site
        log.error("%s : echec du check — %s", site.name, e)
        _record_check(False, 0, human_error(e))
        conn.commit()
        return -1
    finally:
        conn.close()


def _due_for_check(site: SiteConfig, seed: bool) -> bool:
    """Vrai si le site doit etre checke maintenant (cadence par site honoree).

    En mode `once` (boucle CI qui rappelle run_once en continu), on saute un site
    checke depuis moins de son `interval_seconds` : les boutiques rapides ne sont
    plus serialisees derriere les sites Cloudflare lents (FlareSolverr) et sont
    donc revisitees plus souvent. Le seed force le check (amorcage initial)."""
    if seed:
        return True
    with db.connect() as conn:
        elapsed = db.seconds_since_last_check(conn, site.name)
    return elapsed is None or elapsed >= site.interval_seconds


def run_once(cfg: AppConfig, notifier: TelegramNotifier, seed: bool = False) -> None:
    active = 0
    failed = 0
    for site in cfg.sites:
        if not site.enabled:
            continue
        if not _due_for_check(site, seed):
            continue
        active += 1
        if run_site_check(site, cfg, notifier, seed=seed) < 0:
            failed += 1
    # active == 0 : tous les sites etaient « pas encore dus » (cadence) — passage
    # a vide normal, pas une panne. On ne leve donc que si des checks ont tourne.
    if active and failed == active:
        raise RuntimeError("Tous les sites actifs ont echoue pendant ce passage")
    # Nettoie les produits plus vus depuis longtemps (langues exclues, retraits...).
    removed = db.prune_stale(days=2.0)
    if removed:
        log.info("Purge : %d produit(s) perime(s) supprime(s)", removed)
    # Fusionne le WAL dans le fichier principal pour que l'etat mis en cache en CI
    # (seul `data/op_finder.db` est sauvegarde) soit complet au run suivant.
    db.checkpoint()


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
