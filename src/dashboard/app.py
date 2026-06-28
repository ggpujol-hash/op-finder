"""Dashboard de supervision (FastAPI).

Lancer avec :
    uvicorn src.dashboard.app:app --reload --port 8000
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import db
from ..config import load_config

app = FastAPI(title="OP Finder")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _cleanprice(value: str | None) -> str | None:
    """Normalise un prix : retire les labels et, en cas de prix barre + promo,
    garde le dernier montant (le prix courant)."""
    if not value:
        return None
    v = value.replace("\xa0", " ")
    amounts = re.findall(r"\d[\d .]*,\d{2}|\d[\d .]*\d|\d", v)
    if amounts:
        return amounts[-1].strip() + " €"
    return v.strip()


templates.env.filters["cleanprice"] = _cleanprice


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    cfg = load_config()
    sites = [
        {"name": s.name, "enabled": s.enabled, "url": s.url,
         "interval": s.interval_seconds, "type": s.type}
        for s in cfg.sites
    ]
    products = [dict(r) for r in db.recent_products(300)]
    alerts = [dict(r) for r in db.recent_alerts(50)]
    checks = [dict(r) for r in db.recent_checks(40)]

    sites_with_data = {p["site"] for p in products}
    stats = {
        "total": len(products),
        "hot": sum(1 for p in products if p["hot"]),
        "available": sum(1 for p in products if p["available"]),
        "sites_active": sum(1 for s in sites if s["enabled"]),
        "sites_total": len(sites),
        "sites_live": len(sites_with_data),
        "alerts": len(alerts),
        "last_check": checks[0]["ran_at"] if checks else None,
        "checks_ok": sum(1 for c in checks if c["ok"]),
        "checks_total": len(checks),
    }

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "products": products,
            "alerts": alerts,
            "checks": checks,
            "sites": sites,
            "stats": stats,
            "telegram_ok": bool(cfg.telegram_token and cfg.telegram_chat_id),
        },
    )


@app.get("/api/products")
def api_products():
    return [dict(r) for r in db.recent_products(500)]


@app.get("/api/alerts")
def api_alerts():
    return [dict(r) for r in db.recent_alerts(200)]
