"""Activités de l'entreprise : mesure des coûts et des revenus par activité (pour ajuster le budget sur des chiffres réels)."""
from __future__ import annotations

from .db import now


def exists(db, aid: str | None) -> bool:
    return bool(aid) and bool(db.one("SELECT 1 FROM activities WHERE id=?", (aid,)))


def report(db) -> list[dict]:
    """Coûts d'API, revenus et tâches par activité ; la ligne « transverse » regroupe le travail commun."""
    month = now().strftime("%Y-%m")
    rows = [dict(r) for r in db.q("SELECT * FROM activities ORDER BY rowid")] + [
        {"id": None, "name": "Transverse", "description": "Direction, services partagés, travail commun", "status": "active"}]
    out = []
    for a in rows:
        cond, args = ("activity IS NULL", ()) if a["id"] is None else ("activity=?", (a["id"],))
        cost_m = db.one(f"SELECT COALESCE(SUM(usd),0) FROM costs WHERE {cond} AND month=?", (*args, month))[0]
        cost_t = db.one(f"SELECT COALESCE(SUM(usd),0) FROM costs WHERE {cond}", args)[0]
        done = db.one(f"SELECT COUNT(*) FROM tasks WHERE {cond} AND status='done'", args)[0]
        open_ = db.one(f"SELECT COUNT(*) FROM tasks WHERE {cond} AND status IN ('pending','running','blocked')", args)[0]
        if a["id"] is None:
            rev_m = rev_t = 0.0
        else:
            # une vente relève de l'activité de son offre ; sans offre, des services numériques (historique)
            sale_act = "COALESCE((SELECT o.activity FROM offers o WHERE o.id=sales.offer_id), 'services')"
            rev_m = db.one(f"SELECT COALESCE(SUM(amount),0) FROM sales WHERE {sale_act}=? AND currency='EUR' "
                           "AND substr(created_at,1,7)=?", (a["id"], month))[0]
            rev_t = db.one(f"SELECT COALESCE(SUM(amount),0) FROM sales WHERE {sale_act}=? AND currency='EUR'", (a["id"],))[0]
        out.append({**a, "cost_month": round(cost_m, 4), "cost_total": round(cost_t, 4), "revenue_month": round(rev_m, 2),
                    "revenue_total": round(rev_t, 2), "tasks_done": done, "tasks_open": open_})
    return out
