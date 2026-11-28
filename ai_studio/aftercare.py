"""Après la livraison : N jours après, Support prépare un message de suivi (remerciement, demande d'avis, offre
suivante si elle existe) — envoyé seulement après ta validation. Les retours du client sont enregistrés sur la commande ;
un client insatisfait est signalé immédiatement au propriétaire."""
from __future__ import annotations

from datetime import datetime, timedelta

from .db import iso, now


def due(cfg, db) -> list[int]:
    c = cfg.get("aftercare", default={}) or {}
    if not c.get("enabled", True):
        return []
    limit = iso(now() - timedelta(days=int(c.get("days", 3))))
    out = []
    for s in db.q("SELECT rowid AS n, * FROM sales WHERE stage='livree' AND aftercare_at IS NULL AND delivered_at IS NOT NULL "
                  "AND delivered_at<=? AND customer_email IS NOT NULL AND customer_email!=''", (limit,)):
        if s["satisfied"] == 0:                    # client mécontent : pas de message automatique, le propriétaire gère
            db.run("UPDATE sales SET aftercare_at=? WHERE rowid=?", (iso(), s["n"]))
            continue
        tid = db.add_task(f"Suivi après livraison : commande n°{s['n']}"[:120], "support",
                          f"La commande n°{s['n']} ({s['product'] or 'commande'}) a été livrée le {s['delivered_at'][:10]} à "
                          f"{s['customer_email']}.\nPrépare le message de suivi avec send_email, template « suivi_livraison » "
                          f"(fields : commande = {s['n']}, salutation si tu connais le nom, remarque facultative et factuelle sur "
                          "le livrable — relis-le dans livraisons/ si besoin). L'offre suivante et le lien d'avis sont ajoutés "
                          "automatiquement. Un seul message : pas de relance s'il ne répond pas.",
                          priority=3, created_by="systeme", order_ref=s["n"])
        db.run("UPDATE sales SET aftercare_at=? WHERE rowid=?", (iso(), s["n"]))
        db.log("systeme", "commande", f"Commande n°{s['n']} : suivi après livraison à préparer (tâche #{tid})", tid)
        out.append(s["n"])
    return out


def record(cfg, db, n: int, feedback: str, satisfied: bool | None) -> str:
    s = db.one("SELECT rowid AS n, * FROM sales WHERE rowid=?", (int(n),))
    if not s:
        return "Commande introuvable."
    text = ((s["feedback"] + "\n") if s["feedback"] else "") + f"[{iso()[:10]}] {feedback.strip()[:800]}"
    db.run("UPDATE sales SET feedback=?, satisfied=? WHERE rowid=?",
           (text, None if satisfied is None else int(bool(satisfied)), int(n)))
    db.log("support", "commande", f"Commande n°{n} : retour client enregistré" + ("" if satisfied is None else (" (satisfait)" if satisfied else " (INSATISFAIT)")))
    if satisfied is False:
        from .commerce import _notify
        _notify(cfg, db, f"Client insatisfait — commande n°{n}", f"{s['customer_email']} : {feedback.strip()[:200]}", "errors")
    return "Retour enregistré" + (". Le propriétaire est prévenu : propose une solution concrète (correction, précision) sans promettre de remboursement sans son accord (request_owner_decision)." if satisfied is False else ".")
