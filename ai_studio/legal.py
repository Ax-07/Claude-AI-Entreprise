"""Pôle juridique : relecture des actions externes avant la décision du propriétaire.

À chaque cycle (sans coût d'API), les demandes de validation concernées reçoivent une tâche de relecture pour
l'agent juridique. Son avis (legal_review) s'affiche sur la carte de validation. La relecture ne bloque jamais :
le propriétaire peut décider avant qu'elle soit faite.
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
    for a in db.q("SELECT * FROM approvals WHERE status='pending' AND review_task IS NULL ORDER BY id"):
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
                          "ton avis en quelques secondes.", priority=2, created_by="systeme")
        db.run("UPDATE approvals SET review_task=? WHERE id=?", (tid, a["id"]))
        created.append(tid)
    return created
