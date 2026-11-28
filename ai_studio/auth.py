"""Accès au tableau de bord (audit SEC-04) : clé secrète locale + cookie de session.

- La clé est créée au premier démarrage dans data/cle-tableau-de-bord.txt, hors de l'espace de travail des agents.
- Le lien ouvert au démarrage la contient (…/?cle=…) : le serveur pose un cookie HttpOnly, SameSite=Strict, puis
  redirige vers « / » (la clé disparaît de la barre d'adresse et de l'historique de la page).
- Toute route /api exige ce cookie. Un autre programme du PC qui ne connaît pas la clé ne peut ni lire ni agir.
- Les requêtes POST doivent en plus venir de la page elle-même (en-tête Origin identique à l'hôte, contre le CSRF).
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

COOKIE = "studio_session"
MAX_AGE = 365 * 24 * 3600


def key_path(cfg) -> Path:
    return Path(cfg.data_dir) / "cle-tableau-de-bord.txt"


def get_key(cfg) -> str:
    """Clé du tableau de bord (créée au premier appel, lisible par le seul compte utilisateur si possible)."""
    p = key_path(cfg)
    try:
        k = p.read_text(encoding="utf-8").strip()
        if len(k) >= 32:
            return k
    except OSError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    k = secrets.token_urlsafe(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(k + "\n")
    return k


def valid(cfg, candidate: str | None) -> bool:
    return bool(candidate) and hmac.compare_digest(str(candidate).encode(), get_key(cfg).encode())


def cookie_value(headers) -> str | None:
    from http.cookies import CookieError, SimpleCookie
    raw = headers.get("Cookie") or ""
    try:
        c = SimpleCookie(raw)
    except CookieError:
        return None
    return c[COOKIE].value if COOKIE in c else None


def set_cookie_header(cfg) -> str:
    return f"{COOKIE}={get_key(cfg)}; HttpOnly; SameSite=Strict; Path=/; Max-Age={MAX_AGE}"


def login_url(cfg, base: str) -> str:
    return f"{base.rstrip('/')}/?cle={get_key(cfg)}"


LOGIN_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>AI Enterprise Studio — accès</title>
<style>body{font-family:system-ui,sans-serif;background:#0B1029;color:#EEF1FF;display:grid;place-items:center;min-height:100vh;margin:0}
main{max-width:560px;padding:28px;border:1px solid rgba(165,180,252,.35);border-radius:16px;background:rgba(255,255,255,.04)}
code{background:rgba(255,255,255,.08);padding:2px 6px;border-radius:6px}</style></head><body><main>
<h1>Accès protégé</h1>
<p>Le tableau de bord s'ouvre avec ton lien d'accès personnel, ouvert automatiquement au démarrage du moteur.</p>
<p>Pour l'afficher à nouveau : <code>python -m ai_studio lien</code> (ou ouvre <code>data/cle-tableau-de-bord.txt</code>).</p>
<p>Une fois ouvert, ce navigateur reste connecté pendant un an.</p>
</main></body></html>"""
