"""Exécution des actions validées par le propriétaire."""
from __future__ import annotations

import json
import os
import smtplib
from email.message import EmailMessage

from .db import iso
from .tools.business import stripe_request


def send_email(cfg, payload: dict) -> str:
    host = os.environ.get("SMTP_HOST"); user = os.environ.get("SMTP_USER"); pwd = os.environ.get("SMTP_PASSWORD")
    if not (host and user and pwd):
        raise RuntimeError("SMTP non configuré (SMTP_HOST, SMTP_USER, SMTP_PASSWORD dans .env)")
    port = int(os.environ.get("SMTP_PORT", "587"))
    sender = os.environ.get("SMTP_FROM", user)
    msg = EmailMessage()
    msg["From"] = f"{cfg.get('email', 'from_name', default='')} <{sender}>"
    msg["To"] = payload["to"]
    msg["Subject"] = payload["subject"]
    msg.set_content(payload["body"])
    if payload.get("reply_message_id"):
        msg["In-Reply-To"] = payload["reply_message_id"]
        msg["References"] = payload["reply_message_id"]
    if payload.get("attachment"):
        import mimetypes
        base = cfg.workspace.resolve()
        p = (base / payload["attachment"]).resolve()
        if base not in p.parents or not p.is_file():
            raise RuntimeError(f"Pièce jointe introuvable : {payload['attachment']}")
        ctype = (mimetypes.guess_type(p.name)[0] or "application/octet-stream").split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=ctype[0], subtype=ctype[1], filename=p.name)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, pwd); s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(); s.login(user, pwd); s.send_message(msg)
    return f"Email envoyé à {payload['to']}"


def create_payment_link(cfg, payload: dict) -> str:
    params = {
        "currency": "eur",
        "unit_amount": int(round(float(payload["amount_eur"]) * 100)),
        "product_data[name]": payload["product_name"],
    }
    if payload.get("recurring") in ("month", "year"):
        params["recurring[interval]"] = payload["recurring"]
    price = stripe_request("POST", "prices", params)
    link = stripe_request("POST", "payment_links", {"line_items[0][price]": price["id"], "line_items[0][quantity]": 1})
    payload["_result"] = {"id": link["id"], "url": link["url"]}      # relié à la fiche d'offre par decide()
    return f"Lien de paiement créé : {link['url']}"


def publish_page(cfg, payload: dict) -> str:
    from .commerce import publish_page as publish
    return publish(cfg, payload)


EXECUTORS = {"send_email": send_email, "stripe_payment_link": create_payment_link, "publish_page": publish_page}

# Réglages nécessaires à chaque action : s'ils manquent, la validation est refusée et la demande reste en attente,
# au lieu d'échouer (ce qui faisait croire aux agents qu'aucun moyen d'envoi n'existait).
SETUP = {"send_email": ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"), "stripe_payment_link": ("STRIPE_SECRET_KEY",),
         "publish_page": ("GITHUB_PAGES_REPO", "GITHUB_TOKEN")}


class NotReady(Exception):
    """L'action ne peut pas encore être exécutée : un réglage manque dans .env."""


def missing_setup(action: str) -> str:
    return ", ".join(k for k in SETUP.get(action, ()) if not os.environ.get(k))


EMAIL_RE = __import__("re").compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[a-zA-Z]{2,}$")


def apply_edits(action: str, payload: dict, edits: dict | None) -> list[str]:
    """Modifications faites par le propriétaire avant de valider (email : destinataire, objet, texte)."""
    if not edits or action != "send_email":
        return []
    changed = []
    for k in ("to", "subject", "body"):
        if k in edits and str(edits[k]).strip() and str(edits[k]).strip() != str(payload.get(k, "")).strip():
            v = str(edits[k]).strip()
            if k == "to" and not EMAIL_RE.match(v):
                raise ValueError(f"Adresse email invalide : {v}")
            if k == "subject":
                v = " ".join(v.split())[:200]
            payload[k] = v
            changed.append({"to": "destinataire", "subject": "objet", "body": "texte"}[k])
    return changed


def decide(cfg, db, approval_id: int, approve: bool, note: str = "", edits: dict | None = None) -> str:
    a = db.approval(approval_id)
    if not a:
        return "Demande introuvable."
    if a["status"] != "pending":
        return f"Déjà traitée ({a['status']})."
    payload = json.loads(a["payload"])
    changed = apply_edits(a["action"], payload, edits) if approve else []
    # Prise en charge atomique : un double clic (ou deux onglets) ne peut pas exécuter deux fois la même action.
    cur = db.conn.execute("UPDATE approvals SET status='processing' WHERE id=? AND status='pending'", (approval_id,))
    db.conn.commit()
    if cur.rowcount != 1:
        return "Déjà en cours de traitement."
    if changed:
        db.set_approval(approval_id, payload=json.dumps(payload, ensure_ascii=False))
        db.log("proprietaire", "decision", f"Demande #{approval_id} modifiée avant validation : {', '.join(changed)}")
    order = payload.get("order")
    if not order and a["task_id"]:
        row = db.one("SELECT order_ref FROM tasks WHERE id=?", (a["task_id"],))
        order = row[0] if row else None
    if order and note.strip():
        from .commerce import add_owner_note
        add_owner_note(db, order, note)             # la consigne reste attachée à la commande, au-delà de cette tâche
    if not approve:
        db.set_approval(approval_id, status="rejected", decided_at=iso(), result=note or "Refusé")
        outcome = f"REFUSÉ par le propriétaire. {note}".strip()
    elif a["action"] in EXECUTORS:
        missing = missing_setup(a["action"])
        if missing:
            db.set_approval(approval_id, status="pending")        # reste en attente
            raise NotReady(f"Impossible d'exécuter la demande #{approval_id} : {missing} manquant(s) dans .env. "
                           "Elle reste en attente : complète .env, redémarre le moteur, puis valide-la à nouveau.")
        try:
            res = EXECUTORS[a["action"]](cfg, payload)
            db.set_approval(approval_id, status="executed", decided_at=iso(), result=res)
            if a["action"] == "send_email" and payload.get("in_reply_to"):
                db.run("UPDATE inbox SET status='replied' WHERE id=?", (payload["in_reply_to"],))
            if a["action"] == "send_email" and payload.get("order"):
                from .commerce import mark_delivered
                mark_delivered(db, payload["order"], f"Livrable envoyé : {payload.get('attachment')}")
            if a["action"] == "stripe_payment_link" and payload.get("offer_id") and payload.get("_result"):
                db.run("UPDATE offers SET payment_link_id=?, payment_link_url=? WHERE id=?",
                       (payload["_result"]["id"], payload["_result"]["url"], payload["offer_id"]))
            outcome = f"VALIDÉ et exécuté : {res}." + (f" (Modifié par le propriétaire : {', '.join(changed)}.)" if changed else "") + f" {note}".rstrip()
        except Exception as e:
            db.set_approval(approval_id, status="error", decided_at=iso(), result=str(e))
            outcome = f"VALIDÉ mais l'exécution a échoué : {e}"
    else:
        db.set_approval(approval_id, status="approved", decided_at=iso(), result=note or "Validé")
        outcome = f"Réponse du propriétaire : {note or 'OK, validé.'}"
    db.log("proprietaire", "decision", f"Demande #{approval_id} ({a['action']}) : {outcome}")
    # On informe l'agent demandeur via une nouvelle tâche pour qu'il poursuive le travail.
    summary = payload.get("question") or payload.get("subject") or payload.get("product_name") or payload.get("title") or a["action"]
    if a["action"] == "send_email" and payload.get("order") and approve and "exécuté" in outcome:
        return outcome          # livraison faite : rien à poursuivre pour l'agent
    if order and not (a["action"] == "send_email" and payload.get("order") and approve):
        from .commerce import delivery_brief
        outcome += "\n\nÉtat complet de la commande :\n" + delivery_brief(db, order)
    db.add_task(f"Suite à la demande #{approval_id} : {summary}"[:120], a["agent"],
                f"Ta demande #{approval_id} ({a['action']}) a reçu une réponse.\n{outcome}\n"
                f"Contexte initial : {a['reason']}\nPoursuis le travail en conséquence, puis termine la tâche.",
                priority=2, parent_id=a["task_id"], created_by="proprietaire")
    return outcome
