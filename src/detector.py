"""Filtrage par mots-cles + detection des transitions (anti-spam d'alertes)."""
from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlsplit

from .config import AppConfig
from .db import upsert_product
from .models import Event, ProductState, clean_price, parse_amount

# En-deca de cette variation relative, on ne considere PAS un changement de prix
# comme une alerte : micro-ecarts de reformatage/arrondi, ou basculements de
# contexte parasites cote source. 1% laisse passer toute vraie promo.
_PRICE_CHANGE_MIN_RATIO = 0.01


def _is_significant_price_change(prev: str | None, new: str | None) -> bool:
    """Vrai si l'ecart de prix merite une alerte. Compare les MONTANTS (relatif
    au prix precedent) ; a defaut de montant lisible, retombe sur la comparaison
    de chaines nettoyees (comportement historique)."""
    old_amount = parse_amount(prev)
    new_amount = parse_amount(new)
    if old_amount is None or new_amount is None:
        return clean_price(prev) != clean_price(new)
    if old_amount == 0:
        return new_amount != 0
    return abs(new_amount - old_amount) / old_amount > _PRICE_CHANGE_MIN_RATIO


def _matches(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    low = title.lower()
    return any(k in low for k in keywords)


def _is_excluded_type(title: str, cfg: AppConfig) -> bool:
    """Vrai si le produit est d'un TYPE qu'on ne veut pas remonter.

    - Termes simples (`exclude_type_terms`) : sleeves, starter decks... -> exclu
      des qu'un terme apparait dans le titre.
    - Boosters a l'unite (`booster_unit_markers`, ex. "booster", "blister") :
      exclus SEULEMENT si le titre ne porte AUCUN marqueur de lot (`bulk_markers`
      : box, display, boite...). Ainsi "Display ... Booster" et "Booster Box"
      restent, mais "OP16 ... - Booster FR" (pack a l'unite) est retire.
    """
    low = title.lower()
    if any(t in low for t in cfg.exclude_type_terms):
        return True
    if any(u in low for u in cfg.booster_unit_markers) and not any(
        b in low for b in cfg.bulk_markers
    ):
        return True
    return False


# Lettres accentuees du francais. Un nom de produit One Piece ANGLAIS n'en porte
# jamais (les sets EN sont en ASCII : "The Azure Sea's Seven", "Legacy of the
# Master"...). Leur presence dans le titre = reference FR/localisee -> a ecarter
# quand on ne veut que l'anglais. Complete les marqueurs texte pour les noms de
# set FR qui ne portent ni "francais" ni code langue (ex. "L'Heure de la Bataille
# Decisive", "Heroines Edition" FR accentue).
_FRENCH_ACCENTS = set("àâäéèêëîïôöùûüÿçœæ")


def _has_french_accent(text: str) -> bool:
    low = text.lower()
    return any(ch in _FRENCH_ACCENTS for ch in low)


def _has_lang_code(title: str, codes: list[str]) -> bool:
    """Vrai si un code de langue (fr, ko, jp...) apparait comme token isole.

    On tokenise pour ne pas matcher a l'interieur d'un mot (ex. 'fr' dans
    'fruits'). Gere les conventions type 'One Piece FR', '- KO -', '(JP)'.
    """
    if not codes:
        return False
    tokens = set(re.findall(r"[a-z0-9]+", title.lower()))
    return any(c in tokens for c in codes)


def _url_has_lang_code(url: str, codes: list[str]) -> bool:
    if not codes:
        return False
    segments = [s for s in urlsplit(url).path.lower().split("/") if s]
    # Retire un prefixe de locale Shopify ("fr", "en", mais aussi "fr-us",
    # "en-gb"...) : c'est la locale D'AFFICHAGE choisie par le site selon la geo du
    # visiteur (ex. l'IP datacenter CI recoit /fr-us/), pas la langue du PRODUIT.
    # Sans ca, "fr-us" laissait fuiter le token "fr" -> tout le catalogue exclu.
    if segments and re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", segments[0]):
        segments = segments[1:]
    tokens = set(re.findall(r"[a-z0-9]+", " ".join(segments)))
    return any(c in tokens for c in codes)


def apply_filters(states: list[ProductState], cfg: AppConfig) -> list[ProductState]:
    """Garde les produits qui matchent keywords, hors langues exclues ;
    tague hot ceux qui matchent hot_keywords."""
    kept: list[ProductState] = []
    for st in states:
        keywords = cfg.site_keywords.get(st.site, cfg.keywords)
        if not _matches(st.title, keywords):
            continue
        # Exclusion par type de produit (booster a l'unite, sleeves, starter decks).
        if _is_excluded_type(st.title, cfg):
            continue
        # Exclusion par langue (ex. on ne veut que l'anglais -> on retire FR/JP/CN/KR).
        # On scanne le titre + les classes CSS de la fiche (indice de langue). On
        # scanne aussi le slug URL en ignorant le premier segment de locale (/fr/, /en/...).
        lang_text = f"{st.title} {st.tags}"
        # Certains sites imposent un marqueur FR global dans TOUS leurs titres (ex.
        # Philibert : "One Piece Le Jeu de Cartes - ..."). On retire alors ces
        # marqueurs de l'exclusion pour ce site (le reste du filtre langue joue).
        skip = cfg.site_exclude_skip.get(st.site)
        exclude_keywords = (
            [k for k in cfg.exclude_keywords if k not in skip] if skip else cfg.exclude_keywords
        )
        if exclude_keywords and _matches(lang_text, exclude_keywords):
            continue
        # Nom de set FR non tague : accents francais (é, à, ç...) dans le TITRE
        # (pas les tags CSS, qui sont ASCII). Actif seulement quand le francais est
        # exclu. Ecarte "L'Heure de la Bataille Décisive" la ou "français"/"fr"
        # echouent, sans toucher a l'anglais (ASCII). Les noms FR SANS accent
        # ("Les Sept de la Mer d'Azur", "Successeurs") sont couverts par les
        # marqueurs "de la"/"du"/"successeur" dans exclude_keywords.
        if "fr" in cfg.exclude_lang_codes and _has_french_accent(st.title):
            continue
        if _has_lang_code(lang_text, cfg.exclude_lang_codes):
            continue
        if _url_has_lang_code(st.url, cfg.exclude_lang_codes):
            continue
        # Langue par defaut du site : si la boutique est dans une langue exclue
        # (ex. shop FR) et que le produit ne porte AUCUN marqueur d'une langue
        # voulue (EN/ENG...), on considere qu'il est dans la langue du site -> exclu.
        # Opt-in : ne s'active que pour les sites ayant un `lang` configure.
        site_lang = cfg.site_lang.get(st.site, "")
        if site_lang and site_lang in cfg.exclude_lang_codes:
            has_included = _has_lang_code(lang_text, cfg.include_lang_codes) or _url_has_lang_code(
                st.url, cfg.include_lang_codes
            )
            if not has_included:
                continue
        st.hot = _matches(st.title, cfg.hot_keywords)
        kept.append(st)
    return kept


def detect(conn: sqlite3.Connection, states: list[ProductState]) -> list[Event]:
    """Compare les etats observes a la base ; retourne uniquement les transitions.

    - new       : produit jamais vu (nouvelle precommande / nouvelle reference)
    - restock   : passe de indisponible -> disponible, OU precommande qui devient
                  reellement achetable (preorder -> confirmed) : la transition la
                  plus importante a l'approche d'une sortie de set
    - price_change : prix modifie sur un produit deja dispo
    Les etats inchanges ne generent pas d'evenement (donc pas d'alerte).
    """
    events: list[Event] = []
    for st in states:
        prev = conn.execute(
            "SELECT available, price, stock_status FROM products WHERE key = ?", (st.key,)
        ).fetchone()

        changed = False
        if prev is None:
            if st.stock_status == "preorder":
                detail = "Precommande"
            else:
                detail = "Disponible" if st.available else "Reference creee (indispo)"
            events.append(Event(kind="new", state=st, detail=detail))
            changed = True
        else:
            was_available = bool(prev["available"])
            prev_status = prev["stock_status"]
            if st.available and not was_available:
                detail = "Precommande ouverte" if st.stock_status == "preorder" else "De retour en stock"
                events.append(Event(kind="restock", state=st, detail=detail))
                changed = True
            elif prev_status == "preorder" and st.stock_status == "confirmed":
                # Disponible des deux cotes (available reste True), mais la
                # precommande est desormais confirmee/achetable : on alerte malgre
                # tout (sinon le passage "preco -> dispo reelle" serait muet).
                events.append(
                    Event(kind="restock", state=st, detail="Precommande desormais disponible")
                )
                changed = True
            elif st.available and clean_price(st.price) and _is_significant_price_change(
                prev["price"], st.price
            ):
                events.append(
                    Event(kind="price_change", state=st,
                          detail=f"Prix : {prev['price']} -> {st.price}")
                )
                changed = True
            elif prev_status != st.stock_status:
                changed = True

        upsert_product(conn, st, changed=changed)

    conn.commit()
    return events
