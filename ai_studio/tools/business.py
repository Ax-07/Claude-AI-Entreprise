"""Outils métier : emails, Stripe, statistiques.

Toute action qui engage l'entreprise vis-à-vis de l'extérieur (envoyer un email, créer un lien de paiement)
n'est PAS exécutée directement : elle est placée dans la file de validation du propriétaire.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
import urllib.request

from . import tool, ToolContext

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------- emails (validation requise)
@tool("draft_email",
      "Prépare un email à envoyer (prospect, client, partenaire). L'email est mis en attente de validation par le "
      "propriétaire avant envoi. Pour la prospection B2B : message personnalisé, lié à l'activité du destinataire, "
      "avec une phrase permettant de refuser les prochains messages.",
      {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
       "reason": {"type": "string", "description": "Pourquoi cet email (contexte pour le propriétaire)."},
       "in_reply_to": {"type": "integer", "description": "numéro de l'email reçu auquel on répond (garde le fil de discussion)"},
       "attachment": {"type": "string", "description": "fichier de l'espace de travail à joindre (ex. livraisons/rapport.pdf)"}},
      ["to", "subject", "body", "reason"], sensitive=True)
def draft_email(ctx: ToolContext, to: str, subject: str, body: str, reason: str, in_reply_to: int = 0, attachment: str = "") -> str:
    if not EMAIL_RE.match(to.strip()):
        return "Adresse email invalide."
    extra = {}
    if in_reply_to:
        m = ctx.db.one("SELECT id, message_id, subject FROM inbox WHERE id=?", (int(in_reply_to),))
        if not m:
            return f"Email reçu #{in_reply_to} introuvable."
        extra.update(in_reply_to=m["id"], reply_message_id=m["message_id"])
    if attachment:
        from .core import _safe_path
        p = _safe_path(ctx, attachment)
        if not p.is_file():
            return f"Pièce jointe introuvable : {attachment}"
        if p.stat().st_size > 10_000_000:
            return "Pièce jointe trop lourde (10 Mo maximum)."
        extra["attachment"] = attachment
    today = ctx.db.one("SELECT COUNT(*) FROM approvals WHERE action='send_email' AND substr(created_at,1,10)=substr(datetime('now'),1,10)")[0]
    if today >= int(ctx.cfg.get("email", "daily_limit", default=20)):
        return "Refusé : limite quotidienne de brouillons d'emails atteinte."
    aid = ctx.db.add_approval(ctx.agent, ctx.task["id"] if ctx.task is not None else None, "send_email",
                              {"to": to.strip(), "subject": subject, "body": body, **extra}, reason)
    ctx.db.log(ctx.agent, "approval", f"Email pour {to} en attente de validation (#{aid})")
    return f"Email #{aid} en attente de validation par le propriétaire."


# ---------------------------------------------------------------- Stripe
def stripe_request(method: str, path: str, params: dict | None = None) -> dict:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY absente du fichier .env")
    url = "https://api.stripe.com/v1/" + path.lstrip("/")
    data = None
    if params and method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    elif params:
        data = urllib.parse.urlencode(params).encode()
    auth = base64.b64encode(f"{key}:".encode()).decode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


@tool("stripe_balance", "Consulte le solde Stripe de l'entreprise (lecture seule).", {}, requires_env=("STRIPE_SECRET_KEY",))
def stripe_balance(ctx: ToolContext) -> str:
    b = stripe_request("GET", "balance")
    fmt = lambda arr: ", ".join(f"{x['amount']/100:.2f} {x['currency'].upper()}" for x in arr) or "0"
    return f"Disponible : {fmt(b.get('available', []))} | En attente : {fmt(b.get('pending', []))}"


@tool("stripe_recent_payments", "Liste les derniers paiements reçus sur Stripe (lecture seule).",
      {"limit": {"type": "integer", "default": 10}}, requires_env=("STRIPE_SECRET_KEY",))
def stripe_recent_payments(ctx: ToolContext, limit: int = 10) -> str:
    r = stripe_request("GET", "charges", {"limit": min(int(limit), 50)})
    rows = r.get("data", [])
    if not rows:
        return "Aucun paiement."
    return "\n".join(f"{c['id']} | {c['amount']/100:.2f} {c['currency'].upper()} | {c['status']} | {c.get('description') or ''}" for c in rows)


@tool("propose_payment_link",
      "Propose la création d'un lien de paiement Stripe pour une offre (soumis à validation du propriétaire).",
      {"product_name": {"type": "string"}, "amount_eur": {"type": "number"},
       "recurring": {"type": "string", "description": "vide = paiement unique, sinon 'month' ou 'year'"},
       "reason": {"type": "string"}},
      ["product_name", "amount_eur", "reason"], sensitive=True, requires_env=("STRIPE_SECRET_KEY",))
def propose_payment_link(ctx: ToolContext, product_name: str, amount_eur: float, reason: str, recurring: str = "") -> str:
    if not 1 <= float(amount_eur) <= 5000:
        return "Montant hors limites (1 à 5000 €)."
    aid = ctx.db.add_approval(ctx.agent, ctx.task["id"] if ctx.task is not None else None, "stripe_payment_link",
                              {"product_name": product_name, "amount_eur": float(amount_eur), "recurring": recurring}, reason)
    return f"Lien de paiement #{aid} en attente de validation par le propriétaire."


# ---------------------------------------------------------------- statistiques internes
@tool("company_stats", "Statistiques internes : tâches par statut et par agent, dépenses API.", {})
def company_stats(ctx: ToolContext) -> str:
    by_status = ctx.db.q("SELECT status, COUNT(*) n FROM tasks GROUP BY status")
    by_agent = ctx.db.q("SELECT agent, COUNT(*) n, ROUND(SUM(cost_usd),4) c FROM tasks GROUP BY agent")
    lines = ["Tâches par statut : " + ", ".join(f"{r['status']}={r['n']}" for r in by_status)]
    lines += [f"- {r['agent']} : {r['n']} tâches, {r['c'] or 0} $" for r in by_agent]
    lines.append(f"Dépense du mois : {ctx.db.spent('month'):.4f} $")
    return "\n".join(lines)
