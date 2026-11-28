"""Exécution des actions validées par le propriétaire."""
from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import uuid
from email.message import EmailMessage

from .db import iso
from .tools.business import stripe_request


class Uncertain(Exception):
    """L'opération externe a peut-être eu lieu : on ne sait pas (coupure réseau au mauvais moment). Elle n'est
    jamais relancée à l'aveugle : réconciliation (Stripe et GitHub : même clé, sans doublon ; email : recherche dans
    « Messages envoyés »), sinon décision du propriétaire (audit FIN-03)."""


# Opérations qu'on peut relancer sans risque de doublon avec la même clé d'exécution.
SAFE_RETRY = {"stripe_payment_link", "publish_page"}
STUCK_MINUTES = 10           # exécution « en cours » depuis plus longtemps : le programme a été interrompu
MAX_RECONCILE = 5


def send_email(cfg, payload: dict) -> str:
    from .outgoing import check_recipient, frozen_path
    check_recipient(cfg, payload.get("to"))              # mode test : contrôle final, quel que soit le chemin suivi
    attached = frozen_path(cfg, payload) if payload.get("attachment") else None   # empreinte vérifiée AVANT toute connexion
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
    if attached is not None:
        import hashlib
        import mimetypes
        data = attached.read_bytes()
        if hashlib.sha256(data).hexdigest() != payload["attachment_sha256"]:   # relu en mémoire : c'est ce qui part
            raise RuntimeError(f"Pièce jointe modifiée depuis la validation : {payload['attachment']}. Rien n'a été envoyé.")
        ctype = (mimetypes.guess_type(attached.name)[0] or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=ctype[0], subtype=ctype[1], filename=attached.name)
    if payload.get("message_id"):
        msg["Message-ID"] = payload["message_id"]       # permet de le retrouver dans « Messages envoyés » (réconciliation)
    # Avant DATA (connexion, chiffrement, identification) : échec certain, rien n'est parti.
    s = smtplib.SMTP_SSL(host, port, timeout=30) if port == 465 else smtplib.SMTP(host, port, timeout=30)
    try:
        if port != 465:
            s.starttls()
        s.login(user, pwd)
        try:
            s.send_message(msg)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError,
                smtplib.SMTPNotSupportedError, smtplib.SMTPHeloError):
            raise                                        # refus explicite du serveur : pas envoyé
        except (smtplib.SMTPException, OSError) as e:    # coupure pendant l'envoi : le serveur a peut-être accepté
            raise Uncertain(f"connexion perdue pendant l'envoi ({type(e).__name__} : {e})") from e
    finally:
        try:
            s.quit()                                     # une erreur ici (après l'acceptation) ne change rien à l'envoi
        except Exception:
            try:
                s.close()
            except Exception:
                pass
    return f"Email envoyé à {payload['to']}"


def create_payment_link(cfg, payload: dict) -> str:
    params = {
        "currency": "eur",
        "unit_amount": int(round(float(payload["amount_eur"]) * 100)),
        "product_data[name]": payload["product_name"],
    }
    if payload.get("recurring") in ("month", "year"):
        params["recurring[interval]"] = payload["recurring"]
    key = payload.get("_exec_key") or ""
    try:
        price = stripe_request("POST", "prices", params, idempotency_key=f"{key}-price" if key else None)
        link = stripe_request("POST", "payment_links", {"line_items[0][price]": price["id"], "line_items[0][quantity]": 1},
                              idempotency_key=f"{key}-link" if key else None)
    except urllib.error.HTTPError as e:
        if e.code >= 500 or e.code in (409, 429):        # Stripe indisponible ou occupé : sans danger de réessayer (même clé)
            raise Uncertain(f"Stripe {e.code}") from e
        raise RuntimeError(f"Stripe a refusé la demande ({e.code})") from e
    except (urllib.error.URLError, OSError) as e:
        raise Uncertain(f"Stripe injoignable pendant la création ({e})") from e
    payload["_result"] = {"id": link["id"], "url": link["url"]}      # relié à la fiche d'offre par decide()
    return f"Lien de paiement créé : {link['url']}"


def publish_page(cfg, payload: dict) -> str:
    from .commerce import publish_page as publish
    try:
        return publish(cfg, payload)
    except (urllib.error.URLError, OSError) as e:        # republier le même contenu au même endroit est sans danger
        if isinstance(e, urllib.error.HTTPError) and e.code < 500:
            raise
        raise Uncertain(f"GitHub injoignable pendant la publication ({e})") from e


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


def apply_edits(action: str, payload: dict, edits: dict | None, cfg=None) -> list[str]:
    """Modifications faites par le propriétaire avant de valider (email : destinataire, objet, texte)."""
    if not edits or action != "send_email":
        return []
    changed = []
    for k in ("to", "subject", "body"):
        if k in edits and str(edits[k]).strip() and str(edits[k]).strip() != str(payload.get(k, "")).strip():
            v = str(edits[k]).strip()
            if k == "to" and not EMAIL_RE.match(v):
                raise ValueError(f"Adresse email invalide : {v}")
            if k == "to" and cfg is not None:
                from .outgoing import OutgoingError, check_recipient
                try:
                    check_recipient(cfg, v)
                except OutgoingError as e:
                    raise ValueError(f"Destinataire refusé : {e}") from None
            if k == "subject":
                v = " ".join(v.split())[:200]
            payload[k] = v
            changed.append({"to": "destinataire", "subject": "objet", "body": "texte"}[k])
    return changed


def _message_id(approval_id: int, key: str) -> str:
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or ""
    domain = sender.rsplit("@", 1)[-1].strip(" >") if "@" in sender else "ai-studio.local"
    return f"<studio-{approval_id}-{key[:16]}@{domain}>"


def _after_success(cfg, db, action: str, payload: dict) -> None:
    """Suites locales d'une opération réussie (prospect, boîte mail, commande, offre)."""
    if action == "send_email" and payload.get("prospect"):
        from . import prospects
        prospects.on_sent(cfg, db, payload["prospect"], payload.get("template", ""))
    if action == "send_email" and payload.get("in_reply_to"):
        db.run("UPDATE inbox SET status='replied' WHERE id=?", (payload["in_reply_to"],))
    if action == "send_email" and payload.get("order"):
        from .commerce import mark_delivered
        mark_delivered(db, payload["order"], f"Livrable envoyé : {payload.get('attachment')}")
    if action == "stripe_payment_link" and payload.get("offer_id") and payload.get("_result"):
        db.run("UPDATE offers SET payment_link_id=?, payment_link_url=? WHERE id=?",
               (payload["_result"]["id"], payload["_result"]["url"], payload["offer_id"]))


def _mark_executed(cfg, db, approval_id: int, action: str, payload: dict, res: str) -> None:
    # Le succès est enregistré EN PREMIER : si une suite locale échoue ensuite, l'opération n'est pas refaite.
    db.set_approval(approval_id, status="executed", decided_at=iso(), result=res,
                    payload=json.dumps(payload, ensure_ascii=False))
    try:
        _after_success(cfg, db, action, payload)
    except Exception as e:
        db.log("systeme", "erreur", f"Demande #{approval_id} exécutée, mais sa suite locale a échoué : {e}")


def _notify_uncertain(cfg, db, approval_id: int, action: str, why: str) -> None:
    from . import notify
    db.log("systeme", "incertain", f"Demande #{approval_id} ({action}) : issue incertaine — {why}")
    notify.send(cfg, db, f"Demande #{approval_id} : issue incertaine",
                "Coupure pendant l'exécution. Rien ne sera relancé sans vérification : voir le tableau de bord.")


def execute(cfg, db, approval_id: int, action: str, payload: dict) -> tuple[str, str]:
    """Exécute une opération externe validée (audit FIN-03). Renvoie (statut, texte) : executed, error ou uncertain.

    La clé d'exécution et le Message-ID sont enregistrés AVANT l'appel externe : après une coupure, on sait quoi
    vérifier (email dans « Messages envoyés ») ou on relance sans doublon (Stripe et GitHub, même clé)."""
    row = db.approval(approval_id)
    key = row["exec_key"] or uuid.uuid4().hex
    if action == "send_email" and not payload.get("message_id"):
        payload["message_id"] = _message_id(approval_id, key)
    db.set_approval(approval_id, status="processing", exec_key=key, started_at=iso(),
                    payload=json.dumps(payload, ensure_ascii=False))
    run = dict(payload, _exec_key=key)
    try:
        res = EXECUTORS[action](cfg, run)
    except Uncertain as e:
        db.set_approval(approval_id, status="uncertain", result=str(e))
        _notify_uncertain(cfg, db, approval_id, action, str(e))
        return "uncertain", str(e)
    except Exception as e:                       # échec certain : rien n'a été fait
        db.set_approval(approval_id, status="error", decided_at=iso(), result=str(e))
        if action == "send_email" and payload.get("prospect"):
            from . import prospects
            prospects.on_not_sent(db, payload["prospect"])
        return "error", str(e)
    if run.get("_result"):
        payload["_result"] = run["_result"]
    _mark_executed(cfg, db, approval_id, action, payload, res)
    return "executed", res


def sent_copy_exists(message_id: str, connect=None) -> bool | None:
    """Cherche l'email dans le dossier « Messages envoyés » (IMAP, dossier marqué \\Sent). None si impossible."""
    import imaplib
    import re as _re
    from .commerce import imap_settings
    host, user, pwd = imap_settings()
    if not connect and not (host and user and pwd):
        return None
    try:
        M = connect() if connect else imaplib.IMAP4_SSL(host, 993, timeout=30)
        if not connect:
            M.login(user, pwd)
        try:
            typ, boxes = M.list()
            sent = None
            for b in boxes or []:
                line = b.decode("utf-8", "replace") if isinstance(b, bytes) else str(b)
                if "\\Sent" in line:
                    m = _re.search(r'"([^"]+)"\s*$', line) or _re.search(r"(\S+)\s*$", line)
                    sent = m.group(1) if m else None
                    break
            if not sent:
                return None
            M.select(f'"{sent}"', readonly=True)
            typ, data = M.search(None, "HEADER", "Message-ID", message_id)
            return bool(data and data[0] and data[0].split())
        finally:
            try:
                M.logout()
            except Exception:
                pass
    except Exception:
        return None


def reconcile(cfg, db, sent_lookup=sent_copy_exists) -> dict:
    """À chaque cycle : exécutions interrompues → incertaines ; incertaines → résolues si possible, sans doublon."""
    from datetime import timedelta
    from .db import now
    out = {"stuck": 0, "resolved": 0, "waiting": 0}
    limit = iso(now() - timedelta(minutes=STUCK_MINUTES))
    for a in db.q("SELECT * FROM approvals WHERE status='processing' AND (started_at IS NULL OR started_at < ?)", (limit,)):
        db.set_approval(a["id"], status="uncertain", result="Exécution interrompue (programme arrêté pendant l'opération).")
        _notify_uncertain(cfg, db, a["id"], a["action"], "exécution interrompue")
        out["stuck"] += 1
    for a in db.q("SELECT * FROM approvals WHERE status='uncertain' ORDER BY id"):
        payload = json.loads(a["payload"])
        tries = int(a["exec_attempts"] or 0)
        if a["action"] in SAFE_RETRY and tries < MAX_RECONCILE and not missing_setup(a["action"]):
            db.set_approval(a["id"], exec_attempts=tries + 1)
            status, res = execute(cfg, db, a["id"], a["action"], payload)          # même clé : pas de doublon
            if status != "uncertain":
                out["resolved"] += 1
                db.log("systeme", "decision", f"Demande #{a['id']} incertaine réconciliée : {status} — {res}", a["task_id"])
                _follow_up(db, a, payload, f"Demande #{a['id']} : {'exécutée' if status == 'executed' else 'échec'} — {res}")
            continue
        if a["action"] == "send_email" and payload.get("message_id"):
            found = sent_lookup(payload["message_id"])
            if found:
                _mark_executed(cfg, db, a["id"], a["action"], payload, "Email envoyé (retrouvé dans les messages envoyés)")
                db.log("systeme", "decision", f"Demande #{a['id']} : email retrouvé dans les messages envoyés, marqué envoyé", a["task_id"])
                out["resolved"] += 1
                if not payload.get("order"):
                    _follow_up(db, a, payload, f"Ton email #{a['id']} est bien parti (vérifié après une coupure).")
                continue
        out["waiting"] += 1                       # le propriétaire tranche dans le tableau de bord
    return out


def _follow_up(db, a, payload, text: str) -> None:
    summary = payload.get("subject") or payload.get("product_name") or payload.get("title") or a["action"]
    db.add_task(f"Suite à la demande #{a['id']} : {summary}"[:120], a["agent"],
                f"{text}\nContexte initial : {a['reason']}\nPoursuis le travail en conséquence, puis termine la tâche.",
                priority=2, parent_id=a["task_id"], created_by="systeme")


def resolve_uncertain(cfg, db, approval_id: int, done: bool) -> str:
    """Le propriétaire a vérifié : l'opération a bien eu lieu (done) ou non (la demande revient à valider)."""
    a = db.approval(approval_id)
    if not a or a["status"] != "uncertain":
        return "Cette demande n'est pas dans l'état incertain."
    payload = json.loads(a["payload"])
    if done:
        _mark_executed(cfg, db, approval_id, a["action"], payload, "Confirmé par le propriétaire")
        db.log("proprietaire", "decision", f"Demande #{approval_id} incertaine : confirmée comme faite", a["task_id"])
        if not payload.get("order"):
            _follow_up(db, a, payload, f"Ta demande #{approval_id} a bien été exécutée (confirmé par le propriétaire).")
        return "Marquée comme faite."
    payload.pop("message_id", None)               # nouvel essai = nouvelle clé (l'ancienne opération n'a pas eu lieu)
    db.set_approval(approval_id, status="pending", exec_key=None, started_at=None, exec_attempts=0, result=None,
                    payload=json.dumps(payload, ensure_ascii=False))
    db.log("proprietaire", "decision", f"Demande #{approval_id} incertaine : non faite, remise en validation", a["task_id"])
    return "Remise dans les validations : tu peux la valider à nouveau."


def decide(cfg, db, approval_id: int, approve: bool, note: str = "", edits: dict | None = None) -> str:
    a = db.approval(approval_id)
    if not a:
        return "Demande introuvable."
    if a["status"] != "pending":
        return f"Déjà traitée ({a['status']})."
    payload = json.loads(a["payload"])
    changed = apply_edits(a["action"], payload, edits, cfg) if approve else []
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
        if a["action"] == "send_email" and payload.get("prospect"):
            from . import prospects
            prospects.on_not_sent(db, payload["prospect"])
        if a["action"] == "offer":
            from .offers import decide as decide_offer
            decide_offer(db, payload, False)
        outcome = f"REFUSÉ par le propriétaire. {note}".strip()
    elif a["action"] in EXECUTORS:
        missing = missing_setup(a["action"])
        if missing:
            db.set_approval(approval_id, status="pending")        # reste en attente
            raise NotReady(f"Impossible d'exécuter la demande #{approval_id} : {missing} manquant(s) dans .env. "
                           "Elle reste en attente : complète .env, redémarre le moteur, puis valide-la à nouveau.")
        status, res = execute(cfg, db, approval_id, a["action"], payload)
        if status == "uncertain":
            outcome = (f"VALIDÉ, mais l'issue est INCERTAINE : {res}. Rien ne sera relancé automatiquement sans "
                       "vérification ; tu seras prévenu.")
            db.log("proprietaire", "decision", f"Demande #{approval_id} ({a['action']}) : {outcome}", a["task_id"])
            return outcome                       # pas de tâche à l'agent : il ne doit surtout pas refaire la demande
        if status == "executed":
            outcome = f"VALIDÉ et exécuté : {res}." + (f" (Modifié par le propriétaire : {', '.join(changed)}.)" if changed else "") + f" {note}".rstrip()
        else:
            outcome = f"VALIDÉ mais l'exécution a échoué : {res}"
    elif a["action"] == "agent_rule":
        from .audit import apply_rule
        res = apply_rule(db, payload, approval_id)
        db.set_approval(approval_id, status="approved", decided_at=iso(), result=res)
        outcome = f"VALIDÉ : {res}. {note}".strip()
    elif a["action"] == "offer":
        from .offers import decide as decide_offer
        res = decide_offer(db, payload, True)
        db.set_approval(approval_id, status="approved", decided_at=iso(), result=res)
        outcome = f"VALIDÉ : {res}. {note}".strip()
    else:
        db.set_approval(approval_id, status="approved", decided_at=iso(), result=note or "Validé")
        outcome = f"Réponse du propriétaire : {note or 'OK, validé.'}"
    db.log("proprietaire", "decision", f"Demande #{approval_id} ({a['action']}) : {outcome}", a["task_id"])
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
