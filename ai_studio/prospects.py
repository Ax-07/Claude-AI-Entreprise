"""Fichier prospects : chaque contact de prospection, son statut et la prochaine action.

Cycle : nouveau → (email soumis) a_valider → (email envoyé) contacte → relance automatique après N jours (une seule)
→ relance → sans_suite après N jours. Une réponse fait passer en « repondu » (puis interesse / refus / client).
« STOP » ou désinscription → stop : plus jamais contacté. Non distribué → invalide.
"""
from __future__ import annotations

import re
from datetime import timedelta

from .db import iso, now

STATUSES = {
    "nouveau": "Nouveau", "a_valider": "Email à valider", "contacte": "Contacté", "relance": "Relancé",
    "repondu": "A répondu", "interesse": "Intéressé", "client": "Client", "refus": "Refus",
    "stop": "Ne plus contacter", "sans_suite": "Sans suite", "invalide": "Adresse invalide",
}
CLOSED = ("client", "refus", "stop", "sans_suite", "invalide")
NO_CONTACT = ("refus", "stop", "invalide")
QUOTE_RE = re.compile(r"(?im)^(>|le .{4,80} a écrit|on .{4,80} wrote|-{3,} ?(original|message d'origine)|de ?: .*@)")
STOP_RE = re.compile(r"(?i)^\W*stop\W*$|^\W*stop\b|d[ée]sinscri|ne (me|nous) (contactez|sollicitez|[ée]crivez) plus|"
                     r"ne plus recevoir|retirez[- ](moi|nous)|supprimez[- ](moi|nous|mes)")
REFUS_RE = re.compile(r"(?i)pas (int[ée]ress|besoin|pour le moment|pour l'instant)|non merci|aucun int[ée]r[êe]t")


def get(db, email: str):
    return db.one("SELECT * FROM prospects WHERE email=?", ((email or "").strip().lower(),))


def save(db, email: str, source: str, name="", company="", site="", offer="", notes="", agent="", objective_id=None) -> tuple[int, bool]:
    """Crée ou complète un prospect (le statut n'est jamais modifié ici). → (id, créé ?)"""
    email = email.strip().lower()
    p = get(db, email)
    t = iso()
    if p:
        db.run("UPDATE prospects SET name=COALESCE(NULLIF(?,''),name), company=COALESCE(NULLIF(?,''),company), "
               "site=COALESCE(NULLIF(?,''),site), source=COALESCE(NULLIF(?,''),source), offer=COALESCE(NULLIF(?,''),offer), "
               "objective_id=COALESCE(objective_id, ?), notes=CASE WHEN ?='' THEN notes ELSE trim(notes || char(10) || ?) END, "
               "updated_at=? WHERE id=?", (name, company, site, source, offer, objective_id, notes, notes, t, p["id"]))
        return p["id"], False
    pid = db.run("INSERT INTO prospects(email, name, company, site, source, offer, objective_id, notes, created_by, created_at, updated_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (email, name, company, site, source, offer, objective_id, notes, agent, t, t))
    return pid, True


def set_status(db, email: str, status: str, note: str = "", **dates) -> bool:
    if status not in STATUSES:
        raise ValueError(f"Statut inconnu : {status} (possibles : {', '.join(STATUSES)})")
    p = get(db, email)
    if not p:
        return False
    if p["status"] == "stop" and status != "stop":
        return False                        # une opposition est définitive
    sets, vals = ["status=?", "updated_at=?"], [status, iso()]
    for k, v in dates.items():
        sets.append(f"{k}=?"); vals.append(v)
    if note:
        sets.append("notes=trim(notes || char(10) || ?)"); vals.append(f"[{iso()[:10]}] {note}"[:500])
    db.run(f"UPDATE prospects SET {', '.join(sets)} WHERE id=?", (*vals, p["id"]))
    if p["status"] != status:
        db.log("systeme", "prospect", f"Prospect {p['email']} : {STATUSES[p['status']]} → {STATUSES[status]}" + (f" ({note[:80]})" if note else ""))
    return True


def check_send(db, email: str, template: str) -> str | None:
    """Raison de refuser un email de prospection vers cette adresse, ou None."""
    p = get(db, email)
    if not p:
        return ("prospect non enregistré : appelle d'abord save_prospect (adresse, source publique où tu l'as trouvée, "
                "entreprise, site)")
    if not p["source"]:
        return "source de l'adresse inconnue : complète-la avec save_prospect (URL où l'adresse est publiée)"
    st = p["status"]
    if st in NO_CONTACT:
        return f"ce prospect ne doit plus être contacté ({STATUSES[st]})"
    if st == "a_valider":
        return "un email pour ce prospect attend déjà la validation du propriétaire"
    if template == "relance":
        if st != "contacte":
            return f"relance impossible : statut « {STATUSES[st]} » (une relance n'est possible qu'une fois, après un premier contact sans réponse)"
        return None
    if st in ("contacte", "relance"):
        when = f", relance automatique prévue le {p['next_action_at'][:10]}" if p["next_action_at"] else ""
        return f"déjà contacté le {(p['first_contact_at'] or '')[:10]}{when} : n'envoie pas de nouveau premier email"
    if st in ("repondu", "interesse", "client"):
        return f"ce prospect a déjà répondu ({STATUSES[st]}) : réponds dans le fil de son email (in_reply_to)"
    return None


def on_sent(cfg, db, email: str, template: str):
    """Email de prospection réellement envoyé (validé par le propriétaire)."""
    days = int(cfg.get("prospects", "followup_days", default=7))
    t = now()
    if template == "relance":
        close = int(cfg.get("prospects", "close_after_days", default=7))
        set_status(db, email, "relance", last_contact_at=iso(t), next_action_at=iso(t + timedelta(days=close)))
    else:
        p = get(db, email)
        set_status(db, email, "contacte", first_contact_at=(p["first_contact_at"] if p and p["first_contact_at"] else iso(t)),
                   last_contact_at=iso(t), next_action_at=iso(t + timedelta(days=days)))


def on_not_sent(db, email: str):
    """Email refusé par le propriétaire ou en erreur : le prospect revient à son état précédent."""
    p = get(db, email)
    if p and p["status"] == "a_valider":
        set_status(db, email, "contacte" if p["first_contact_at"] else "nouveau", "email non envoyé")


def own_text(body: str) -> str:
    """Le texte écrit par l'expéditeur, sans le message cité en dessous."""
    m = QUOTE_RE.search(body or "")
    return (body[:m.start()] if m else body or "").strip()


def on_reply(db, email: str, body: str) -> str | None:
    """Email reçu d'un prospect. → nouveau statut (stop, refus, repondu) ou None si ce n'est pas un prospect."""
    p = get(db, email)
    if not p or p["status"] in ("stop", "client"):
        return None
    text = own_text(body)
    first = "\n".join(text.splitlines()[:6])
    status = "stop" if STOP_RE.search(first) else "refus" if REFUS_RE.search(first) else "repondu"
    if status == "repondu" and p["status"] in ("interesse",):
        status = "interesse"
    set_status(db, email, status, {"stop": "a demandé à ne plus être contacté", "refus": "a décliné"}.get(status, "a répondu"),
               replied_at=iso(), next_action_at=None)
    return status


def became_client(db, email: str):
    if get(db, email):
        set_status(db, email, "client", "paiement reçu", next_action_at=None)


def due_followups(cfg, db) -> dict:
    """Relances à faire (une seule) et prospects à classer sans suite. Appelé à chaque cycle."""
    out = {"followups": [], "closed": []}
    t = iso()
    for p in db.q("SELECT * FROM prospects WHERE status='contacte' AND next_action_at IS NOT NULL AND next_action_at<=?", (t,)):
        tid = db.add_task(f"Relance prospect : {p['company'] or p['email']}"[:120], "ventes",
                          f"Le prospect {p['name'] or ''} <{p['email']}> ({p['company'] or p['site'] or '—'}) n'a pas répondu au premier "
                          f"email du {(p['first_contact_at'] or '')[:10]}.\n"
                          "Prépare UNE relance courte avec send_email, template « relance » (email_templates) : objet exact et "
                          "date du premier email (list_prospects / mémoire), rappel de l'intérêt pour lui, sans pression. "
                          "C'est la seule relance : s'il ne répond pas, il sera classé sans suite automatiquement.",
                          priority=3, created_by="systeme", objective_id=p["objective_id"])
        db.run("UPDATE prospects SET next_action_at=NULL, updated_at=? WHERE id=?", (t, p["id"]))
        db.log("systeme", "prospect", f"Relance de {p['email']} à préparer (tâche #{tid})", tid)
        out["followups"].append(p["email"])
    for p in db.q("SELECT * FROM prospects WHERE status='relance' AND next_action_at IS NOT NULL AND next_action_at<=?", (t,)):
        set_status(db, p["email"], "sans_suite", "pas de réponse après la relance", next_action_at=None)
        out["closed"].append(p["email"])
    return out


def listing(db, status: str = "", query: str = "", limit: int = 50) -> list[dict]:
    sql, args = "SELECT * FROM prospects WHERE 1=1", []
    if status:
        sql += " AND status=?"; args.append(status)
    if query:
        q = f"%{query.lower()}%"
        sql += " AND (email LIKE ? OR lower(name) LIKE ? OR lower(company) LIKE ? OR lower(site) LIKE ?)"; args += [q, q, q, q]
    return [dict(r) for r in db.q(sql + " ORDER BY updated_at DESC LIMIT ?", (*args, int(limit)))]


def counts(db) -> dict:
    return {r[0]: r[1] for r in db.q("SELECT status, COUNT(*) FROM prospects GROUP BY status")}
