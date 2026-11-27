"""Cybersécurité défensive : contrôles de configuration, détection d'anomalies, emails suspects.

Règles : on ne lit ni n'affiche jamais la valeur d'un secret (seulement son type) ; aucune action offensive ;
tout se fait sans appel aux modèles d'IA (coût nul), l'agent Cybersécurité interprète les résultats.
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import timedelta
from pathlib import Path

from .db import iso, now

SECRET_PATTERNS = [
    (re.compile(r"\bsk_live_[0-9A-Za-z]{10,}"), "clé Stripe réelle"),
    (re.compile(r"\brk_live_[0-9A-Za-z]{10,}"), "clé Stripe restreinte réelle"),
    (re.compile(r"\bsk-(?:proj-|ant-)?[0-9A-Za-z_-]{20,}"), "clé d'API d'IA"),
    (re.compile(r"\bghp_[0-9A-Za-z]{20,}"), "jeton GitHub classique"),
    (re.compile(r"\bgithub_pat_[0-9A-Za-z_]{20,}"), "jeton GitHub"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "clé AWS"),
]

INJECTION = [
    (re.compile(r"(ignore|oublie|disregard|forget)\b.{0,30}\b(instructions?|consignes?|règles|rules|prompt)", re.I), "demande d'ignorer des consignes"),
    (re.compile(r"\b(system prompt|prompt système|tu es maintenant|you are now|act as|agis comme)\b", re.I), "tentative de changer le rôle de l'agent"),
    (re.compile(r"(envoie|send|donne|give|transmets?).{0,40}(cl[ée]s?|keys?|mots? de passe|passwords?|tokens?|identifiants|credentials)", re.I), "demande de secrets"),
    (re.compile(r"(virement|wire transfer|gift card|carte cadeau|bitcoin|crypto).{0,60}(urgent|immédiat|today|aujourd)", re.I), "demande de paiement urgente"),
    (re.compile(r"https?://(\d{1,3}\.){3}\d{1,3}|https?://[^\s/]*xn--", re.I), "lien suspect (adresse IP ou nom de domaine déguisé)"),
]


def suspicious_text(text: str) -> list[str]:
    return [why for rx, why in INJECTION if rx.search(text or "")]


def find_secrets(text: str) -> list[str]:
    return sorted({why for rx, why in SECRET_PATTERNS if rx.search(text or "")})


def _git_tracked(root: Path, name: str) -> bool:
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", name], cwd=root, capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def config_audit(cfg, db) -> list[tuple[str, str]]:
    """Retourne des constats (niveau, message) : critique, attention ou ok. Aucun secret n'apparaît."""
    from .config import ROOT
    root = Path(getattr(cfg, "root", ROOT))
    out: list[tuple[str, str]] = []
    gi = (root / ".gitignore").read_text(encoding="utf-8") if (root / ".gitignore").exists() else ""
    for name in (".env", "config.json", "data/"):
        if name not in gi:
            out.append(("critique", f"{name} n'est pas exclu de Git (.gitignore) : risque de publier des secrets ou des données."))
        elif _git_tracked(root, name.rstrip("/")):
            out.append(("critique", f"{name} est suivi par Git : il partirait sur GitHub au prochain envoi."))
    stripe = os.environ.get("STRIPE_SECRET_KEY", "")
    if stripe.startswith("sk_live_"):
        out.append(("critique", "Clé Stripe réelle à pleins pouvoirs (sk_live) : la remplacer par une clé restreinte (rk_live)."))
    elif stripe.startswith("sk_test_"):
        out.append(("attention", "Clé Stripe de test à pleins pouvoirs : préférer une clé restreinte (rk_test) dès maintenant."))
    gh = os.environ.get("GITHUB_TOKEN", "")
    if gh.startswith("ghp_"):
        out.append(("attention", "Jeton GitHub classique (accès à tous les dépôts) : préférer un jeton « fine-grained » limité au dépôt du site."))
    smtp_host, smtp_pwd = os.environ.get("SMTP_HOST", ""), os.environ.get("SMTP_PASSWORD", "")
    if "gmail" in smtp_host and smtp_pwd and len(smtp_pwd.replace(" ", "")) != 16:
        out.append(("critique", "Le mot de passe Gmail ne ressemble pas à un mot de passe d'application (16 lettres) : ne jamais utiliser le vrai mot de passe du compte."))
    b = cfg["budget"]
    if float(b["daily_usd"]) > float(b["monthly_usd"]):
        out.append(("attention", "Le plafond quotidien dépasse le plafond mensuel."))
    if not cfg.get("notifications", "windows", default=True):
        out.append(("attention", "Notifications désactivées : une alerte de sécurité passerait inaperçue."))
    last = db.kv_get("last_backup_day")
    if cfg.get("backup", "enabled", default=True) and last and now().strftime("%Y-%m-%d") > last:
        try:
            from datetime import datetime
            if now().date() - datetime.strptime(last, "%Y-%m-%d").date() > timedelta(days=2):
                out.append(("attention", f"Dernière sauvegarde le {last} : plus de 2 jours."))
        except ValueError:
            pass
    # secrets recopiés par erreur dans les livrables ou la mémoire des agents
    leaks = set()
    ws = Path(cfg.workspace)
    if ws.exists():
        for p in ws.rglob("*"):
            if p.is_file() and p.suffix.lower() in (".md", ".txt", ".html", ".json", ".csv") and p.stat().st_size < 2_000_000:
                for why in find_secrets(p.read_text(encoding="utf-8", errors="ignore")):
                    leaks.add(f"{why} dans {p.relative_to(ws).as_posix()}")
    for m in db.q("SELECT id, content FROM memory"):
        for why in find_secrets(m["content"]):
            leaks.add(f"{why} dans la mémoire (entrée #{m['id']})")
    for leak in sorted(leaks):
        out.append(("critique", f"Secret recopié : {leak}. L'effacer et changer la clé concernée."))
    if not out:
        out.append(("ok", "Aucun problème de configuration détecté."))
    return out


def anomalies(db) -> list[str]:
    """Écarts par rapport à l'habitude (7 derniers jours) : dépenses, échecs, envois, emails suspects."""
    out = []
    today = now().strftime("%Y-%m-%d")
    week = [(now() - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(1, 8)]
    spent_today = db.one("SELECT COALESCE(SUM(usd),0) FROM costs WHERE day=?", (today,))[0]
    avg = db.one(f"SELECT COALESCE(SUM(usd),0) FROM costs WHERE day IN ({','.join('?'*7)})", week)[0] / 7
    if spent_today > 0.2 and spent_today > 3 * max(avg, 0.01):
        out.append(f"Dépense du jour {spent_today:.3f} $, plus de 3 fois la moyenne ({avg:.3f} $/jour).")
    for r in db.q("SELECT agent, SUM(usd) s FROM costs WHERE day=? GROUP BY agent HAVING s > 0.15", (today,)):
        out.append(f"L'agent {r['agent']} a dépensé {r['s']:.3f} $ aujourd'hui.")
    since = iso(now() - timedelta(hours=24))
    failed = db.one("SELECT COUNT(*) FROM tasks WHERE status='failed' AND updated_at>=?", (since,))[0]
    if failed >= 3:
        out.append(f"{failed} tâches en échec sur 24 h.")
    sent = db.one("SELECT COUNT(*) FROM approvals WHERE action='send_email' AND status='executed' AND decided_at>=?", (since,))[0]
    if sent >= 15:
        out.append(f"{sent} emails envoyés sur 24 h.")
    sus = db.one("SELECT COUNT(*) FROM inbox WHERE reason LIKE 'suspect%' AND recorded_at>=?", (since,))[0]
    if sus:
        out.append(f"{sus} email(s) suspect(s) reçu(s) sur 24 h.")
    return out or ["Aucune anomalie."]


def check(cfg, db) -> list[str]:
    """À chaque cycle, sans coût : alerte une seule fois par problème critique de configuration."""
    sent = []
    for level, msg in config_audit(cfg, db):
        if level != "critique":
            continue
        key = "sec_alert_" + str(abs(hash(msg)) % 10**9)
        if db.kv_get(key):
            continue
        db.kv_set(key, iso())
        db.log("securite", "alerte", msg)
        from . import notify
        notify.send(cfg, db, "Alerte de sécurité", msg)
        sent.append(msg)
    return sent
