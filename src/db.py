"""Persistance SQLite : etat courant des produits + journal des alertes."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import ProductState

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "op_finder.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    key         TEXT PRIMARY KEY,
    site        TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    price       TEXT,
    available   INTEGER NOT NULL,
    stock_status TEXT NOT NULL DEFAULT 'inferred',
    hot         INTEGER NOT NULL DEFAULT 0,
    miss_count  INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    last_change TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    key       TEXT NOT NULL,
    site      TEXT NOT NULL,
    title     TEXT NOT NULL,
    url       TEXT NOT NULL,
    kind      TEXT NOT NULL,
    detail    TEXT,
    sent_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    site      TEXT NOT NULL,
    ok        INTEGER NOT NULL,
    items     INTEGER NOT NULL DEFAULT 0,
    message   TEXT,
    ran_at    TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout + WAL : les checks tournent en threads paralleles (BackgroundScheduler)
    # et se partagent la base -> evite les "database is locked".
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
        if "stock_status" not in cols:
            conn.execute(
                "ALTER TABLE products ADD COLUMN stock_status TEXT NOT NULL DEFAULT 'inferred'"
            )
        if "miss_count" not in cols:
            conn.execute(
                "ALTER TABLE products ADD COLUMN miss_count INTEGER NOT NULL DEFAULT 0"
            )


def get_product(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    cur = conn.execute("SELECT * FROM products WHERE key = ?", (key,))
    return cur.fetchone()


def upsert_product(conn: sqlite3.Connection, st: ProductState, changed: bool) -> None:
    now = _now()
    existing = get_product(conn, st.key)
    if existing is None:
        conn.execute(
            """INSERT INTO products
               (key, site, title, url, price, available, stock_status, hot,
                first_seen, last_seen, last_change)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (st.key, st.site, st.title, st.url, st.price, int(st.available),
             st.stock_status, int(st.hot), now, now, now),
        )
    else:
        conn.execute(
            """UPDATE products SET title=?, url=?, price=?, available=?, stock_status=?, hot=?,
               last_seen=?, last_change=? WHERE key=?""",
            (st.title, st.url, st.price, int(st.available), st.stock_status, int(st.hot),
             now, now if changed else existing["last_change"], st.key),
        )


def recent_alert_exists(
    conn: sqlite3.Connection, key: str, kind: str, detail: str | None, within_hours: float = 12.0
) -> bool:
    """Vrai si une alerte identique (meme produit + type + detail) a deja ete
    envoyee dans la fenetre donnee. Sert d'anti-spam : un produit qui oscille
    dispo<->rupture (scrape intermittent cote CI) ne doit pas reemettre 8 fois
    le meme restock. Le detail est inclus pour qu'un *vrai* changement de prix
    (detail different) passe quand meme."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=within_hours)).isoformat()
    row = conn.execute(
        "SELECT 1 FROM alerts WHERE key = ? AND kind = ? "
        "AND COALESCE(detail, '') = COALESCE(?, '') AND sent_at >= ? LIMIT 1",
        (key, kind, detail, cutoff),
    ).fetchone()
    return row is not None


def log_alert(conn: sqlite3.Connection, st: ProductState, kind: str, detail: str) -> None:
    conn.execute(
        """INSERT INTO alerts (key, site, title, url, kind, detail, sent_at)
           VALUES (?,?,?,?,?,?,?)""",
        (st.key, st.site, st.title, st.url, kind, detail, _now()),
    )


def last_check_ok(conn: sqlite3.Connection, site: str) -> bool | None:
    """Statut (ok/ko) du DERNIER check d'un site, tous statuts confondus.

    Sert a detecter une transition de sante (sain -> casse -> retabli) pour
    pousser une alerte : un scraper aveugle (0 produit, selecteurs casses,
    Cloudflare) est un angle mort silencieux qui fait rater tous les restocks du
    site. None = jamais checke."""
    row = conn.execute(
        "SELECT ok FROM checks WHERE site = ? ORDER BY ran_at DESC LIMIT 1", (site,)
    ).fetchone()
    return bool(row["ok"]) if row else None


def consecutive_failures(conn: sqlite3.Connection, site: str) -> int:
    """Nombre de checks KO consecutifs les plus recents d'un site (0 si le dernier
    est OK ou si jamais checke).

    Sert a n'annoncer une panne qu'apres plusieurs echecs d'affilee : une boutique
    qui blippe hors-ligne quelques minutes (Cloudflare passager, throttle, timeout)
    revient seule au check suivant et ne doit pas generer d'alerte 'hors ligne'
    puis '_de nouveau operationnel_' — c'etait la source du spam."""
    n = 0
    for row in conn.execute(
        "SELECT ok FROM checks WHERE site = ? ORDER BY ran_at DESC, id DESC LIMIT 100",
        (site,),
    ):
        if row["ok"]:
            break
        n += 1
    return n


def failure_streak_bounds(
    conn: sqlite3.Connection, site: str
) -> tuple[datetime, datetime] | None:
    """Bornes temporelles de la serie de checks KO consecutifs la plus recente.

    Retourne (debut, fin) = ran_at du plus ancien et du plus recent KO de la serie
    en cours, ou None si le dernier check est OK / le site n'a jamais ete checke.

    Sert a n'annoncer une panne qu'apres une duree soutenue (24h) plutot qu'apres
    un nombre de checks : une boutique peut blipper KO plusieurs passages d'affilee
    sur quelques minutes sans meriter d'alerte 'hors ligne'."""
    oldest = None
    newest = None
    for row in conn.execute(
        "SELECT ok, ran_at FROM checks WHERE site = ? ORDER BY ran_at DESC, id DESC LIMIT 500",
        (site,),
    ):
        if row["ok"]:
            break
        ts = datetime.fromisoformat(row["ran_at"])
        if newest is None:
            newest = ts
        oldest = ts
    if oldest is None:
        return None
    return oldest, newest


def seconds_since_last_check(conn: sqlite3.Connection, site: str) -> float | None:
    """Secondes ecoulees depuis le dernier check (ok OU ko) d'un site.

    Permet d'honorer `interval_seconds` par site meme en mode `once` (boucle CI) :
    on evite de re-checker un site trop tot, ce qui liberait du temps pour les
    boutiques rapides au lieu de tout serialiser derriere les sites Cloudflare
    lents (FlareSolverr). None = jamais checke."""
    row = conn.execute(
        "SELECT ran_at FROM checks WHERE site = ? ORDER BY ran_at DESC LIMIT 1", (site,)
    ).fetchone()
    if not row:
        return None
    last = datetime.fromisoformat(row["ran_at"])
    return (datetime.now(timezone.utc) - last).total_seconds()


def available_map(conn: sqlite3.Connection, site: str) -> dict[str, bool]:
    """Disponibilite actuelle en base, par cle produit, pour un site.

    Sert au garde-fou anti-bascule massive : si un check fait passer d'un coup la
    quasi-totalite des produits dispo -> rupture, c'est presque surement une
    regression de selecteur (ex. `in_stock_selector` dont la classe a change),
    pas un vrai sell-out global. On l'ignore pour ne pas emettre plus tard une
    tempete de faux restocks au retour a la normale."""
    return {
        row["key"]: bool(row["available"])
        for row in conn.execute(
            "SELECT key, available FROM products WHERE site = ?", (site,)
        )
    }


def checkpoint() -> None:
    """Fusionne le WAL dans le fichier principal (PRAGMA wal_checkpoint TRUNCATE).

    En CI l'etat n'est mis en cache que via `data/op_finder.db` (pas le `-wal`) :
    sans checkpoint, la queue de transactions du WAL n'est pas sauvegardee et
    l'etat restaure au run suivant est incomplet (re-alertes / anti-doublon
    perdu)."""
    with connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def last_successful_items(conn: sqlite3.Connection, site: str) -> int | None:
    """Nb de produits du dernier check REUSSI d'un site (None si jamais reussi).

    Sert a reperer une panne silencieuse : un site qui retournait N>0 et tombe a
    0 a probablement vu ses selecteurs casser (HTTP 200 mais 0 fiche)."""
    row = conn.execute(
        "SELECT items FROM checks WHERE site = ? AND ok = 1 ORDER BY ran_at DESC LIMIT 1",
        (site,),
    ).fetchone()
    return row["items"] if row else None


def reconcile_missing(
    conn: sqlite3.Connection, site: str, seen_keys: set[str], threshold: int = 2
) -> int:
    """Bascule en rupture les produits d'un site absents de N checks successifs.

    Beaucoup de boutiques retirent les produits en rupture de la page categorie
    (ou les renvoient au-dela de max_pages). Sans cela, ils restent available=1
    en base : leur retour en stock ne declencherait jamais de restock. On
    incremente un compteur d'absences (remis a 0 des qu'on les revoit) et, au seuil,
    on les marque 'out' pour qu'un retour ulterieur soit bien detecte.

    Seuil = 2 (et non 3) : les displays convoites partent vite et sont souvent
    retires du listing des l'epuisement ; les marquer 'out' plus tot garantit
    qu'un restock rapide sera bien detecte. Le faux risque (produit juste absent
    d'une page a cause d'un scrape partiel) est deja couvert en amont par
    RECONCILE_MIN_RATIO cote runner : reconcile n'est appele que sur un scrape
    juge complet.

    A n'appeler que sur un check REUSSI et non vide (sinon une panne de selecteurs
    ferait basculer tout le catalogue en rupture). Retourne le nb bascule.
    """
    flipped = 0
    rows = conn.execute(
        "SELECT key, available, miss_count FROM products WHERE site = ?", (site,)
    ).fetchall()
    for row in rows:
        if row["key"] in seen_keys:
            if row["miss_count"]:
                conn.execute(
                    "UPDATE products SET miss_count = 0 WHERE key = ?", (row["key"],)
                )
            continue
        new_miss = row["miss_count"] + 1
        if row["available"] and new_miss >= threshold:
            conn.execute(
                "UPDATE products SET available = 0, stock_status = 'out', miss_count = ? "
                "WHERE key = ?",
                (new_miss, row["key"]),
            )
            flipped += 1
        else:
            conn.execute(
                "UPDATE products SET miss_count = ? WHERE key = ?", (new_miss, row["key"])
            )
    return flipped


def log_check(conn: sqlite3.Connection, site: str, ok: bool, items: int, message: str = "") -> None:
    conn.execute(
        "INSERT INTO checks (site, ok, items, message, ran_at) VALUES (?,?,?,?,?)",
        (site, int(ok), items, message, _now()),
    )


def prune_stale(days: float = 2.0) -> int:
    """Supprime les produits plus vus depuis `days` jours (ex. lignes d'une langue
    desormais exclue, ou produits retires du catalogue). Retourne le nb supprime."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with connect() as conn:
        cur = conn.execute("DELETE FROM products WHERE last_seen < ?", (cutoff,))
        conn.commit()
        return cur.rowcount


def recent_products(limit: int = 200) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM products ORDER BY hot DESC, last_change DESC LIMIT ?", (limit,)
        ).fetchall()


def recent_alerts(limit: int = 100) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM alerts ORDER BY sent_at DESC LIMIT ?", (limit,)
        ).fetchall()


def recent_checks(limit: int = 50) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM checks ORDER BY ran_at DESC LIMIT ?", (limit,)
        ).fetchall()
