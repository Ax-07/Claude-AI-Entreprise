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
OPT_OUT_RE = re.compile(r"(?i)\bstop\b|d[ée]sinscri|ne plus recevoir|vous opposer|plus contact|plus sollicit")


def sender_footer(cfg, body: str) -> tuple[str, list[str]]:
    """Signature légale (identité de l'expéditeur) et phrase d'opposition, si l'agent ne les a pas mises."""
    from ..config import sender_legal
    legal = sender_legal(cfg)
    added, lines = [], []
    name = (legal.get("name") or "").strip()
    if name and name.lower() not in body.lower():
        lines.append(f"{name} — entrepreneur individuel (micro-entreprise)"
                     + (f", SIRET {legal['siret']}" if legal.get("siret") else ""))
        if legal.get("email") and legal["email"].lower() not in body.lower():
            lines.append(legal["email"])
        added.append("signature de l'expéditeur")
    if not OPT_OUT_RE.search(body):
        lines.append("Si vous ne souhaitez plus recevoir de message de notre part, répondez simplement « STOP ».")
        added.append("phrase d'opposition")
    if lines:
        body = body.rstrip() + "\n\n--\n" + "\n".join(lines)
    return body, added


@tool("send_email",
      "Envoie un email (prospect, client, partenaire). C'est le moyen d'envoyer un email, et il suffit : l'email part "
      "AUTOMATIQUEMENT dès que le propriétaire clique sur « Valider » dans le tableau de bord. Ne passe pas par un "
      "brouillon dans un fichier ni par request_owner_decision pour le faire valider, et ne demande jamais au "
      "propriétaire de l'envoyer lui-même. Pour la prospection B2B : message personnalisé, lié à l'activité du "
      "destinataire. S'il existe un modèle adapté (email_templates), utilise-le : donne template et fields au lieu de "
      "subject et body (prix, délai et offre sont repris du catalogue ; signature et phrase d'opposition sont ajoutées "
      "automatiquement).",
      {"to": {"type": "string"}, "subject": {"type": "string", "description": "objet (sans modèle)"},
       "body": {"type": "string", "description": "texte (sans modèle)"},
       "template": {"type": "string", "description": "identifiant du modèle (email_templates)"},
       "fields": {"type": "object", "additionalProperties": {"type": "string"}, "description": "valeurs des champs du modèle, ex. {\"site\": \"ax-07.fr\", \"accroche\": \"…\"}"},
       "reason": {"type": "string", "description": "Pourquoi cet email (contexte pour le propriétaire)."},
       "in_reply_to": {"type": "integer", "description": "numéro de l'email reçu auquel on répond (garde le fil de discussion)"},
       "attachment": {"type": "string", "description": "fichier de l'espace de travail à joindre (ex. livraisons/rapport.pdf)"}},
      ["to", "reason"], sensitive=True)
def send_email(ctx: ToolContext, to: str, reason: str, subject: str = "", body: str = "", in_reply_to: int = 0,
               attachment: str = "", template: str = "", fields: dict | None = None) -> str:
    if not EMAIL_RE.match(to.strip()):
        return "Adresse email invalide."
    used = None
    if template:
        from .. import email_templates
        try:
            subject, body, t = email_templates.render(ctx.cfg, ctx.db, template, fields if isinstance(fields, dict) else {})
        except (KeyError, ValueError) as e:
            return f"Refusé : {str(e).strip(chr(39))}. Corrige les champs et rappelle send_email."
        used = t
        reason = f"[Modèle : {t.get('name', t['id'])}] {reason}"
    elif not (subject.strip() and body.strip()):
        return "Donne subject et body, ou un modèle (template + fields : voir email_templates)."
    from ..config import test_recipients
    allowed = test_recipients(ctx.cfg)
    if allowed is not None and to.strip().lower() not in allowed:
        return (f"Refusé : mode test actif, seuls ces destinataires sont autorisés : {', '.join(allowed) or 'aucun'}. "
                "N'essaie pas d'autre adresse ; indique-le dans ton résultat.")
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
    added = []
    client = ctx.task is not None and ctx.task["order_ref"] is not None
    if not in_reply_to and not client:       # premier contact (prospection) : identité de l'expéditeur et droit d'opposition ajoutés d'office
        body, added = sender_footer(ctx.cfg, body)
        if added:
            reason = f"{reason}\n[Ajouté automatiquement : {', '.join(added)}]"
    _, missing = __import__("ai_studio.commerce", fromlist=["legal_footer"]).legal_footer(ctx.cfg)
    if allowed is not None:
        reason = f"[MODE TEST — identité fictive] {reason}"
    elif not in_reply_to and not client and missing:
        reason = f"{reason}\n⚠ Identité de l'expéditeur incomplète ({', '.join(missing)}) : renseigne company.legal dans config.json."
    today = ctx.db.one("SELECT COUNT(*) FROM approvals WHERE action='send_email' AND substr(created_at,1,10)=substr(datetime('now'),1,10)")[0]
    if today >= int(ctx.cfg.get("email", "daily_limit", default=20)):
        return "Refusé : limite quotidienne de brouillons d'emails atteinte."
    aid = ctx.db.add_approval(ctx.agent, ctx.task["id"] if ctx.task is not None else None, "send_email",
                              {"to": to.strip(), "subject": subject, "body": body, **extra,
                               **({"template": used["id"]} if used else {})}, reason)
    ctx.db.log(ctx.agent, "approval", f"Email pour {to} en attente de validation (#{aid})")
    from ..approvals import missing_setup
    later = missing_setup("send_email")
    note = (f" La boîte d'envoi n'est pas encore configurée ({later}) : l'email partira dès que le propriétaire "
            "l'aura configurée et validé l'email.") if later else ""
    return (f"Email #{aid} en attente de validation : il partira automatiquement dès que le propriétaire le validera. "
            f"Rien d'autre à faire pour l'envoyer, ni relance ni demande au propriétaire.{note}")


@tool("email_templates", "Liste les modèles d'emails (prospection, relance, réponse) et les champs à remplir pour send_email.", {})
def email_templates(ctx: ToolContext) -> str:
    from .. import email_templates as et
    try:
        return et.describe(ctx.cfg, ctx.db)
    except ValueError as e:
        return str(e)


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
      "Propose la création d'un lien de paiement Stripe pour une offre du catalogue (soumis à validation du "
      "propriétaire). Indique offer = nom de la fiche d'offre : le nom et le prix sont repris de la fiche, et chaque "
      "vente sera rattachée à cette offre pour la livraison.",
      {"offer": {"type": "string", "description": "nom de l'offre du catalogue (recommandé)"},
       "product_name": {"type": "string"}, "amount_eur": {"type": "number"},
       "recurring": {"type": "string", "description": "vide = paiement unique, sinon 'month' ou 'year'"},
       "reason": {"type": "string"}},
      ["reason"], sensitive=True, requires_env=("STRIPE_SECRET_KEY",))
def propose_payment_link(ctx: ToolContext, reason: str, offer: str = "", product_name: str = "", amount_eur: float = 0,
                         recurring: str = "") -> str:
    offer_id = None
    if offer:
        o = ctx.db.one("SELECT * FROM offers WHERE lower(name)=lower(?) AND status='active'", (offer.strip(),))
        if not o:
            return f"Offre « {offer} » introuvable dans le catalogue (list_offers, save_offer)."
        offer_id, product_name, amount_eur = o["id"], o["name"], o["price_eur"]
    if not product_name:
        return "Indique l'offre du catalogue (offer), ou à défaut product_name et amount_eur."
    if not 1 <= float(amount_eur) <= 5000:
        return "Montant hors limites (1 à 5000 €)."
    aid = ctx.db.add_approval(ctx.agent, ctx.task["id"] if ctx.task is not None else None, "stripe_payment_link",
                              {"product_name": product_name, "amount_eur": float(amount_eur), "recurring": recurring,
                               "offer_id": offer_id}, reason)
    note = "" if offer_id else " Sans fiche d'offre, les agents ne sauront pas quoi livrer : crée-la avec save_offer."
    return f"Lien de paiement #{aid} en attente de validation : il sera créé automatiquement dès que le propriétaire validera.{note}"


# ---------------------------------------------------------------- statistiques internes
@tool("company_stats", "Statistiques internes : tâches par statut et par agent, dépenses API.", {})
def company_stats(ctx: ToolContext) -> str:
    by_status = ctx.db.q("SELECT status, COUNT(*) n FROM tasks GROUP BY status")
    by_agent = ctx.db.q("SELECT agent, COUNT(*) n, ROUND(SUM(cost_usd),4) c FROM tasks GROUP BY agent")
    lines = ["Tâches par statut : " + ", ".join(f"{r['status']}={r['n']}" for r in by_status)]
    lines += [f"- {r['agent']} : {r['n']} tâches, {r['c'] or 0} $" for r in by_agent]
    lines.append(f"Dépense du mois : {ctx.db.spent('month'):.4f} $")
    return "\n".join(lines)
