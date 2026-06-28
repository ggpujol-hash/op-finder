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
    hot         INTEGER NOT NULL DEFAULT 0,
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def get_product(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    cur = conn.execute("SELECT * FROM products WHERE key = ?", (key,))
    return cur.fetchone()


def upsert_product(conn: sqlite3.Connection, st: ProductState, changed: bool) -> None:
    now = _now()
    existing = get_product(conn, st.key)
    if existing is None:
        conn.execute(
            """INSERT INTO products
               (key, site, title, url, price, available, hot, first_seen, last_seen, last_change)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (st.key, st.site, st.title, st.url, st.price, int(st.available),
             int(st.hot), now, now, now),
        )
    else:
        conn.execute(
            """UPDATE products SET title=?, url=?, price=?, available=?, hot=?,
               last_seen=?, last_change=? WHERE key=?""",
            (st.title, st.url, st.price, int(st.available), int(st.hot),
             now, now if changed else existing["last_change"], st.key),
        )


def log_alert(conn: sqlite3.Connection, st: ProductState, kind: str, detail: str) -> None:
    conn.execute(
        """INSERT INTO alerts (key, site, title, url, kind, detail, sent_at)
           VALUES (?,?,?,?,?,?,?)""",
        (st.key, st.site, st.title, st.url, kind, detail, _now()),
    )


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
