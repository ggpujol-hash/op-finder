"""Envoi des alertes via Telegram Bot API."""
from __future__ import annotations

from html import escape
import logging
import time
from urllib.parse import urlsplit

import httpx

from .models import Event

log = logging.getLogger("notifier")

# Nombre de tentatives d'envoi avant d'abandonner. Un envoi qui echoue n'est PAS
# journalise comme alerte -> il sera re-tente au passage suivant, mais on retente
# aussi immediatement (blip reseau, 429 passager) pour ne pas repousser de
# plusieurs minutes une alerte de restock time-critical.
SEND_ATTEMPTS = 3

_ICONS = {"new": "\U0001F195", "restock": "♻️", "price_change": "\U0001F4B0"}
_LABELS = {"new": "NOUVEAU", "restock": "RESTOCK", "price_change": "PRIX"}


def _safe_text(value: str | None) -> str:
    return escape(value or "", quote=False)


def _safe_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return "https://example.invalid/"
    return escape(value, quote=True)


def format_message(ev: Event) -> str:
    st = ev.state
    icon = _ICONS.get(ev.kind, "\U0001F514")
    label = _LABELS.get(ev.kind, ev.kind.upper())
    fire = "\U0001F525 " if st.hot else ""
    lines = [
        f"{fire}{icon} <b>{_safe_text(label)}</b> — {_safe_text(st.site)}",
        f"<b>{_safe_text(st.title)}</b>",
    ]
    if st.price:
        lines.append(f"Prix : {_safe_text(st.price)}")
    if ev.detail:
        lines.append(_safe_text(ev.detail))
    lines.append(f'<a href="{_safe_url(st.url)}">Voir le produit</a>')
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        if not self.enabled:
            log.warning("Telegram non configure (token/chat_id manquant) — alertes en log seulement.")

    def _post(self, payload: dict) -> bool:
        """Envoie un message Telegram avec retry sur erreur transitoire.

        Retourne True seulement si Telegram a accepte le message. Un False garanti
        « non delivre » : l'appelant NE DOIT PAS journaliser l'alerte comme envoyee,
        sinon l'anti-doublon la bloquerait et le restock serait perdu."""
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        last_exc: httpx.HTTPError | None = None
        for attempt in range(SEND_ATTEMPTS):
            try:
                resp = httpx.post(url, json=payload, timeout=15.0)
                resp.raise_for_status()
                return True
            except httpx.HTTPStatusError as e:
                last_exc = e
                # 429 : Telegram indique parfois un delai dans retry_after.
                retry_after = 0.0
                if e.response.status_code == 429:
                    try:
                        retry_after = float(
                            e.response.json().get("parameters", {}).get("retry_after", 0)
                        )
                    except (ValueError, KeyError, TypeError):
                        retry_after = 0.0
                # 4xx autres que 429 (ex. 400 message mal forme) : inutile de retenter.
                elif e.response.status_code < 500:
                    log.error("Echec envoi Telegram (definitif) : %s", e)
                    return False
                if attempt + 1 >= SEND_ATTEMPTS:
                    break
                time.sleep(max(retry_after, 1.5 * (attempt + 1)))
            except httpx.HTTPError as e:
                last_exc = e
                if attempt + 1 >= SEND_ATTEMPTS:
                    break
                time.sleep(1.5 * (attempt + 1))
        log.error("Echec envoi Telegram apres %d tentatives : %s", SEND_ATTEMPTS, last_exc)
        return False

    def send(self, ev: Event) -> bool:
        text = format_message(ev)
        if not self.enabled:
            log.info("[ALERTE non envoyee] %s", text.replace("\n", " | "))
            return False
        return self._post({
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        })

    def send_text(self, text: str) -> bool:
        """Message libre (test de connexion, sante d'un site)."""
        if not self.enabled:
            log.info("[MSG non envoye] %s", text)
            return False
        return self._post(
            {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        )
