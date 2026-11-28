"""Pôle juridique : relecture des actions externes AVANT qu'elles arrivent au propriétaire.

Une demande concernée (page publique, lien de paiement… voir legal_review.actions) passe en statut « review » : elle
n'est pas encore visible dans les validations. Le Juridique la relit :
- « ok » → elle arrive au propriétaire, avec l'avis ;
- « corrections » ou « risque » → elle est renvoyée à l'agent qui l'a faite, avec les corrections ; il corrige et
  refait la demande, qui repasse par le Juridique. Après N allers-retours (legal_review.max_rounds), la demande arrive
  au propriétaire avec l'avis, pour qu'il tranche (pas de boucle sans fin).
Si la relecture n'est pas faite (échec, délai dépassé), la demande arrive au propriétaire, avec cette mention.
"""
from __future__ import annotations

import json

VERDICTS = {"ok": "✅ Conforme", "corrections": "⚠️ À corriger", "risque": "⛔ Risque juridique"}


def needs_review(cfg, a) -> bool:
    c = cfg["legal_review"]
    if not c.get("enabled", True) or a["agent"] == "juridique":
        return False
    if a["action"] in c.get("actions", []):
        return True
    if a["action"] == "send_email" and c.get("prospection_emails", False):
        p = json.loads(a["payload"])
        return not p.get("in_reply_to") and not p.get("order")       # premier contact = prospection
    return False


def queue_reviews(cfg, db) -> list[int]:
    created = []
    for a in db.q("SELECT * FROM approvals WHERE status='pending' AND review_task IS NULL AND COALESCE(notified,0)=0 ORDER BY id"):
        if not needs_review(cfg, a):
            db.run("UPDATE approvals SET review_task=0 WHERE id=?", (a["id"],))      # 0 = pas de relecture
            continue
        p = json.loads(a["payload"])
        if a["action"] == "publish_page":
            what = (f"Page web publique {p.get('url')} — titre « {p.get('title')} ». Contenu : fichier {p.get('file')} "
                    "(lis-le avec read_file ; le pied de page légal est ajouté automatiquement).")
        elif a["action"] == "stripe_payment_link":
            what = f"Lien de paiement : {p.get('product_name')} à {p.get('amount_eur')} €" + (f" / {p['recurring']}" if p.get("recurring") else "")
        elif a["action"] == "send_email":
            what = f"Email à {p.get('to')}, objet « {p.get('subject')} » :\n{p.get('body', '')[:4000]}"
        else:
            what = json.dumps(p, ensure_ascii=False)[:3000]
        tid = db.add_task(f"Relecture juridique de la demande #{a['id']}"[:120], "juridique",
                          f"Avant la décision du propriétaire, relis cette action proposée par l'agent {a['agent']} "
                          f"(motif : {a['reason']}).\n\n{what}\n\n"
                          "Vérifie : mentions obligatoires, promesses exagérées ou trompeuses, droit de la consommation "
                          "(prix TTC, droit de rétractation, CGV), RGPD et prospection, propriété intellectuelle. Puis "
                          f"appelle legal_review(approval_id={a['id']}, verdict='ok' | 'corrections' | 'risque', notes) "
                          "avec des notes courtes et concrètes (quoi corriger, comment). Reste bref : le propriétaire lit "
                          "ton avis en quelques secondes. « corrections » renvoie la demande à l'agent pour qu'il la corrige "
                          "avant qu'elle n'arrive au propriétaire : ne l'utilise que pour ce qui doit vraiment changer, avec "
                          "le texte exact à ajouter ou retirer.", priority=1, created_by="systeme")
        db.run("UPDATE approvals SET review_task=?, status='review' WHERE id=?", (tid, a["id"]))
        db.log("juridique", "juridique", f"Demande #{a['id']} ({a['action']}) en relecture juridique avant ta décision (tâche #{tid})", tid)
        created.append(tid)
    release_stale(cfg, db)
    return created


def release(db, a, note: str) -> None:
    db.set_approval(a["id"], status="pending", review=note)


def release_stale(cfg, db) -> list[int]:
    """Relecture impossible (tâche en échec) ou trop longue : la demande arrive au propriétaire avec cette mention."""
    from datetime import datetime, timedelta
    from .db import now
    hours = float(cfg.get("legal_review", "max_wait_hours", default=3))
    out = []
    for a in db.q("SELECT * FROM approvals WHERE status='review'"):
        t = db.task(a["review_task"]) if a["review_task"] else None
        late = now() - datetime.fromisoformat(a["created_at"]) > timedelta(hours=hours)
        if t is None or t["status"] in ("failed", "blocked", "cancelled", "done") or late:
            why = ("tâche de relecture en échec" if t is not None and t["status"] in ("failed", "blocked", "cancelled")
                   else "relecture terminée sans avis" if t is not None and t["status"] == "done"
                   else f"pas d'avis après {hours:g} h" if late else "tâche de relecture introuvable")
            release(db, a, f"Relecture juridique non faite ({why}) : décide sans avis, ou demande une modification.")
            db.log("juridique", "juridique", f"Demande #{a['id']} transmise sans avis juridique ({why})", a["review_task"])
            out.append(a["id"])
    return out


def correction_round(db, a) -> int:
    """Nombre de corrections déjà demandées par le Juridique sur cette demande (et celles qu'elle remplace)."""
    return int(db.kv_get(f"legal_round_task_{a['task_id']}", "0") or 0) if a["task_id"] else 0


def apply_verdict(cfg, db, a, verdict: str, notes: str) -> str:
    """Avis du Juridique sur une demande en relecture : vers le propriétaire, ou retour à l'agent pour correction."""
    text = f"{VERDICTS[verdict]} — {notes.strip()[:1200]}"
    if a["status"] == "pending":                    # déjà transmise (relecture trop tardive) : l'avis s'ajoute à la carte
        db.set_approval(a["id"], review=text)
        return "Avis enregistré : il s'affiche sur la demande de validation."
    rounds = correction_round(db, a)
    maxr = int(cfg.get("legal_review", "max_rounds", default=2))
    if verdict == "ok" or rounds >= maxr:
        release(db, a, text + (f" (après {rounds} correction(s) : le propriétaire tranche)" if verdict != "ok" else ""))
        db.log("juridique", "juridique", f"Avis sur la demande #{a['id']} : {VERDICTS[verdict]} → transmise au propriétaire")
        return ("Avis enregistré : la demande est transmise au propriétaire avec ton avis."
                + (" (Nombre maximal de corrections atteint : c'est lui qui tranche.)" if verdict != "ok" else ""))
    p = json.loads(a["payload"])
    db.set_approval(a["id"], status="rejected", review=text, decided_at=__import__("ai_studio.db", fromlist=["iso"]).iso(),
                    result=f"Renvoyée par le Juridique pour correction : {notes.strip()[:800]}")
    if a["action"] == "send_email" and p.get("prospect"):
        from . import prospects
        prospects.on_not_sent(db, p["prospect"])
    tid = db.add_task(f"Corriger la demande #{a['id']} (avis du Juridique)"[:120], a["agent"],
                      f"Le Juridique a relu ta demande #{a['id']} ({a['action']}, motif : {a['reason']}) AVANT qu'elle "
                      f"n'arrive au propriétaire. Avis : {VERDICTS[verdict]}\nCorrections demandées : {notes.strip()[:1200]}\n\n"
                      f"Demande d'origine : {json.dumps({k: v for k, v in p.items() if k not in ('html', 'qa')}, ensure_ascii=False)[:1500]}\n\n"
                      "Applique les corrections, puis refais la demande avec le même outil : elle repassera par le Juridique, "
                      "puis arrivera au propriétaire. Si une correction est impossible ou contraire à une consigne du "
                      "propriétaire, refais la demande telle quelle en l'expliquant dans le motif (reason) : le propriétaire tranchera.",
                      priority=1, parent_id=a["task_id"], created_by="juridique")
    db.kv_set(f"legal_round_task_{tid}", str(rounds + 1))
    db.log("juridique", "juridique", f"Demande #{a['id']} renvoyée à {a['agent']} pour correction (tâche #{tid})", tid)
    return f"Avis enregistré : la demande est renvoyée à {a['agent']} pour correction (tâche #{tid}), avant le propriétaire."
