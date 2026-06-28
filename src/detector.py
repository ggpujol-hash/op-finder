"""Filtrage par mots-cles + detection des transitions (anti-spam d'alertes)."""
from __future__ import annotations

import re
import sqlite3

from .config import AppConfig
from .db import upsert_product
from .models import Event, ProductState


def _matches(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    low = title.lower()
    return any(k in low for k in keywords)


def _has_lang_code(title: str, codes: list[str]) -> bool:
    """Vrai si un code de langue (fr, ko, jp...) apparait comme token isole.

    On tokenise pour ne pas matcher a l'interieur d'un mot (ex. 'fr' dans
    'fruits'). Gere les conventions type 'One Piece FR', '- KO -', '(JP)'.
    """
    if not codes:
        return False
    tokens = set(re.findall(r"[a-z0-9]+", title.lower()))
    return any(c in tokens for c in codes)


def apply_filters(states: list[ProductState], cfg: AppConfig) -> list[ProductState]:
    """Garde les produits qui matchent keywords, hors langues exclues ;
    tague hot ceux qui matchent hot_keywords."""
    kept: list[ProductState] = []
    for st in states:
        if not _matches(st.title, cfg.keywords):
            continue
        # Exclusion par langue (ex. on ne veut que l'anglais -> on retire FR/JP/CN/KR).
        # On scanne le titre + les classes CSS de la fiche (indice de langue), mais
        # pas l'URL (les segments de locale type "/fr/" provoqueraient des faux positifs).
        lang_text = f"{st.title} {st.tags}"
        if cfg.exclude_keywords and _matches(lang_text, cfg.exclude_keywords):
            continue
        if _has_lang_code(lang_text, cfg.exclude_lang_codes):
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
