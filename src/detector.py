"""Filtrage par mots-cles + detection des transitions (anti-spam d'alertes)."""
from __future__ import annotations

import sqlite3

from .config import AppConfig
from .db import upsert_product
from .models import Event, ProductState


def _matches(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    low = title.lower()
    return any(k in low for k in keywords)


def apply_filters(states: list[ProductState], cfg: AppConfig) -> list[ProductState]:
    """Garde les produits qui matchent keywords ; tague hot ceux qui matchent hot_keywords."""
    kept: list[ProductState] = []
    for st in states:
        if not _matches(st.title, cfg.keywords):
            continue
        st.hot = _matches(st.title, cfg.hot_keywords)
        kept.append(st)
    return kept


def detect(conn: sqlite3.Connection, states: list[ProductState]) -> list[Event]:
    """Compare les etats observes a la base ; retourne uniquement les transitions.

    - new       : produit jamais vu (nouvelle precommande / nouvelle reference)
    - restock   : passe de indisponible -> disponible
    - price_change : prix modifie sur un produit deja dispo
    Les etats inchanges ne generent pas d'evenement (donc pas d'alerte).
    """
    events: list[Event] = []
    for st in states:
        prev = conn.execute(
            "SELECT available, price FROM products WHERE key = ?", (st.key,)
        ).fetchone()

        changed = False
        if prev is None:
            detail = "Disponible" if st.available else "Reference creee (indispo)"
            events.append(Event(kind="new", state=st, detail=detail))
            changed = True
        else:
            was_available = bool(prev["available"])
            if st.available and not was_available:
                events.append(Event(kind="restock", state=st, detail="De retour en stock"))
                changed = True
            elif st.available and prev["price"] != st.price and st.price:
                events.append(
                    Event(kind="price_change", state=st,
                          detail=f"Prix : {prev['price']} -> {st.price}")
                )
                changed = True

        upsert_product(conn, st, changed=changed)

    conn.commit()
    return events
