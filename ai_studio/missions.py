"""Tableau des missions : objectifs et commandes, avancement réel des tâches, signaux d'anomalie et vérification
par le propriétaire (« terminée pour de bon » ou « à reprendre »)."""
from __future__ import annotations

from datetime import datetime, timedelta

from .db import iso, now

OPEN = ("pending", "running", "blocked")
STUCK_MINUTES = 30            # une tâche « en cours » depuis plus longtemps est suspecte
IDLE_HOURS = 24               # mission active sans aucune activité depuis


def _short(text: str, n: int = 70) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def labels_for_tasks(db, task_ids) -> dict:
    """Libellé de mission de chaque tâche : « Mission #3 · … » (objectif) ou « Commande n°2 · … »."""
    ids = sorted({int(t) for t in task_ids if t})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = db.q(f"SELECT t.id, t.objective_id, t.order_ref, o.text AS otext, s.product AS product FROM tasks t "
                f"LEFT JOIN objectives o ON o.id=t.objective_id LEFT JOIN sales s ON s.rowid=t.order_ref WHERE t.id IN ({marks})", ids)
    out = {}
    for r in rows:
        if r["objective_id"]:
            out[r["id"]] = {"kind": "objectif", "id": r["objective_id"], "label": f"Mission #{r['objective_id']} · {_short(r['otext'], 60)}"}
        elif r["order_ref"]:
            out[r["id"]] = {"kind": "commande", "id": r["order_ref"], "label": f"Commande n°{r['order_ref']} · {_short(r['product'] or 'commande', 50)}"}
    return out


def _age_minutes(ts: str | None) -> float:
    if not ts:
        return 0
    try:
        return (now() - datetime.fromisoformat(ts)).total_seconds() / 60
    except ValueError:
        return 0


def _summary(db, where: str, arg) -> dict:
    rows = db.q(f"SELECT id, status, updated_at, result FROM tasks WHERE {where}", (arg,))
    counts = {s: 0 for s in ("pending", "running", "blocked", "done", "failed")}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total = len(rows)
    last = max((r["updated_at"] for r in rows), default=None)
    stuck = [r["id"] for r in rows if r["status"] == "running" and _age_minutes(r["updated_at"]) > STUCK_MINUTES]
    return {"counts": counts, "total": total, "progress": round(100 * counts["done"] / total) if total else 0,
            "last_activity": last, "stuck": stuck, "failed_ids": [r["id"] for r in rows if r["status"] == "failed"]}


def _issues(declared_done: bool, active: bool, s: dict) -> list[str]:
    c, issues = s["counts"], []
    if c["failed"]:
        issues.append(f"{c['failed']} tâche(s) en échec")
    if s["stuck"]:
        issues.append(f"{len(s['stuck'])} tâche(s) bloquée(s) en cours depuis plus de {STUCK_MINUTES} min")
    if c["blocked"]:
        issues.append(f"{c['blocked']} tâche(s) bloquée(s)")
    if declared_done and (c["pending"] or c["running"]):
        issues.append("déclarée terminée alors que des tâches restent ouvertes")
    if declared_done and s["total"] and not c["done"]:
        issues.append("déclarée terminée sans aucune tâche réussie")
    if active and not declared_done and s["last_activity"] and _age_minutes(s["last_activity"]) > IDLE_HOURS * 60 and not (c["pending"] or c["running"]):
        issues.append(f"aucune activité depuis plus de {IDLE_HOURS} h")
    return issues


def missions(db) -> list[dict]:
    out = []
    for o in db.q("SELECT * FROM objectives ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, id DESC"):
        s = _summary(db, "objective_id=?", o["id"])
        done = o["status"] == "done"
        out.append({"kind": "objectif", "id": o["id"], "title": _short(o["text"], 90), "text": o["text"], "activity": o["activity"],
                    "state": {"active": "En cours", "paused": "En pause", "done": "Terminée (agents)"}[o["status"]], "status": o["status"],
                    "declared_done": done, **s, "issues": _issues(done, o["status"] == "active", s),
                    "owner_check": o["owner_check"], "owner_note": o["owner_note"], "checked_at": o["checked_at"], "created_at": o["created_at"]})
    for r in db.q("SELECT rowid AS n, * FROM sales WHERE stage IS NOT NULL AND stage!='historique' ORDER BY created_at DESC LIMIT 40"):
        s = _summary(db, "order_ref=?", r["n"])
        done = r["stage"] == "livree"
        late = not done and r["due_at"] and r["due_at"] < iso()
        st = {"a_livrer": "À livrer", "attente_client": "Attente client", "livree": "Livrée (agents)"}.get(r["stage"], r["stage"])
        issues = _issues(done, True, s) + (["échéance dépassée"] if late else [])
        out.append({"kind": "commande", "id": r["n"], "title": f"Commande n°{r['n']} · {r['product'] or 'commande'}", "text": r["product"] or "",
                    "activity": "services", "state": st, "status": r["stage"], "declared_done": done, **s, "issues": issues,
                    "owner_check": r["owner_check"], "owner_note": r["owner_notes"], "checked_at": r["checked_at"], "created_at": r["created_at"],
                    "test": not r["livemode"]})
    return out


def detail(db, kind: str, mid: int) -> dict:
    m = next((x for x in missions(db) if x["kind"] == kind and x["id"] == int(mid)), None)
    if not m:
        raise KeyError("Mission introuvable")
    col = "objective_id" if kind == "objectif" else "order_ref"
    m["tasks"] = [dict(r) for r in db.q(f"SELECT id, parent_id, title, agent, status, priority, result, created_by, updated_at, attempts "
                                        f"FROM tasks WHERE {col}=? ORDER BY id", (int(mid),))]
    for t in m["tasks"]:
        t["result"] = _short(t["result"] or "", 400)
    return m


def verify(cfg, db, kind: str, mid: int, verdict: str, note: str = "") -> str:
    """Le propriétaire confirme qu'une mission est vraiment terminée, ou la renvoie au PDG avec ce qui ne va pas."""
    m = detail(db, kind, mid)
    note = (note or "").strip()
    if verdict not in ("ok", "issue"):
        raise ValueError("Verdict inconnu")
    if verdict == "issue" and len(note) < 5:
        raise ValueError("Explique ce qui ne va pas : le PDG s'en servira pour reprendre la mission.")
    if kind == "objectif":
        if verdict == "ok":
            db.run("UPDATE objectives SET owner_check='ok', owner_note=?, checked_at=?, status='done', done_at=COALESCE(done_at, ?) WHERE id=?",
                   (note or None, iso(), iso(), mid))
        else:
            db.run("UPDATE objectives SET owner_check='issue', owner_note=?, checked_at=?, status='active' WHERE id=?", (note, iso(), mid))
    else:
        from .commerce import add_owner_note
        if verdict == "ok":
            db.run("UPDATE sales SET owner_check='ok', checked_at=? WHERE rowid=?", (iso(), mid))
        else:
            add_owner_note(db, mid, "Mission à reprendre : " + note)
            db.run("UPDATE sales SET owner_check='issue', checked_at=?, stage='a_livrer' WHERE rowid=?", (iso(), mid))
    db.log("proprietaire", "mission", f"{m['title']} : " + ("vérifiée, terminée." if verdict == "ok" else f"à reprendre — {note}"))
    if verdict == "ok":
        return "Mission confirmée comme terminée."
    lines = "\n".join(f"- #{t['id']} [{t['status']}] {t['agent']} : {t['title']}" + (f" → {t['result'][:160]}" if t["result"] else "")
                      for t in m["tasks"][-15:])
    db.add_task(f"Reprendre : {m['title']}"[:120], "pdg",
                f"Le propriétaire a vérifié « {m['title']} » et considère qu'elle N'EST PAS terminée.\n"
                f"Ce qui ne va pas, selon lui : {note}\n\nTâches de la mission :\n{lines or '(aucune)'}\n\n"
                "Analyse la cause (tâche en échec, livrable incomplet, mauvaise compréhension…), corrige-la en relançant ou en "
                "déléguant le travail nécessaire, puis ne déclare la mission terminée que lorsque le résultat répond vraiment à la demande.",
                priority=1, created_by="proprietaire", objective_id=mid if kind == "objectif" else None,
                order_ref=mid if kind == "commande" else None)
    return "Mission renvoyée au PDG avec ta remarque."
