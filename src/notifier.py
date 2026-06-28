"""Envoi des alertes via Telegram Bot API."""
from __future__ import annotations

from html import escape
import logging
from urllib.parse import urlsplit

import httpx

from .models import Event

log = logging.getLogger("notifier")

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

    def send(self, ev: Event) -> bool:
        text = format_message(ev)
        if not self.enabled:
            log.info("[ALERTE non envoyee] %s", text.replace("\n", " | "))
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        try:
            resp = httpx.post(url, json=payload, timeout=15.0)
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            log.error("Echec envoi Telegram : %s", e)
            return False

    def send_text(self, text: str) -> bool:
        """Message libre (test de connexion, resume de demarrage)."""
        if not self.enabled:
            log.info("[MSG non envoye] %s", text)
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            resp = httpx.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=15.0,
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            log.error("Echec envoi Telegram : %s", e)
            return False
