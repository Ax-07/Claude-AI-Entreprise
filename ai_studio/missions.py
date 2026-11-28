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


def _waiting(db) -> dict:
    """Validations en attente, rattachées à leur mission : {("objectif", id) | ("commande", n): [n° de validation]}."""
    import json
    out = {}
    for a in db.q("SELECT a.id, a.payload, t.objective_id, t.order_ref FROM approvals a LEFT JOIN tasks t ON t.id=a.task_id "
                  "WHERE a.status='pending'"):
        order = json.loads(a["payload"]).get("order") or a["order_ref"]
        if order:
            out.setdefault(("commande", int(order)), []).append(a["id"])
        elif a["objective_id"]:
            out.setdefault(("objectif", a["objective_id"]), []).append(a["id"])
    return out


def _issues(declared_done: bool, active: bool, s: dict, paused: bool = False, waiting=()) -> list[str]:
    c, issues = s["counts"], []
    if c["failed"]:
        issues.append(f"{c['failed']} tâche(s) en échec")
    if s["stuck"]:
        issues.append(f"{len(s['stuck'])} tâche(s) bloquée(s) en cours depuis plus de {STUCK_MINUTES} min")
    if c["blocked"] and not paused:
        issues.append(f"{c['blocked']} tâche(s) bloquée(s)")
    nobody = s["total"] and not (c["pending"] or c["running"] or c["blocked"])
    if active and not declared_done and nobody and not waiting:
        issues.append("toutes les tâches sont finies mais la mission n'est pas clôturée : plus personne n'y travaille")
    if declared_done and (c["pending"] or c["running"]):
        issues.append("déclarée terminée alors que des tâches restent ouvertes")
    if declared_done and s["total"] and not c["done"]:
        issues.append("déclarée terminée sans aucune tâche réussie")
    if active and not declared_done and s["last_activity"] and _age_minutes(s["last_activity"]) > IDLE_HOURS * 60 and not (c["pending"] or c["running"]):
        issues.append(f"aucune activité depuis plus de {IDLE_HOURS} h")
    return issues


def _to_check(m: dict) -> bool:
    """À vérifier par le propriétaire : déclarée terminée, ou toutes ses tâches finies sans rien en attente."""
    if m["archived"] or m["owner_check"] == "ok" or not m["total"]:
        return False
    c = m["counts"]
    return m["declared_done"] or not (c["pending"] or c["running"] or c["blocked"] or m["waiting"])


def _costs(db) -> dict:
    """Coût d'API cumulé par mission : {("objectif", id) | ("commande", n): usd}."""
    out = {}
    for r in db.q("SELECT t.objective_id o, t.order_ref n, SUM(c.usd) usd FROM costs c JOIN tasks t ON t.id=c.task_id "
                  "WHERE t.objective_id IS NOT NULL OR t.order_ref IS NOT NULL GROUP BY t.objective_id, t.order_ref"):
        key = ("commande", r["n"]) if r["n"] else ("objectif", r["o"])
        out[key] = out.get(key, 0) + (r["usd"] or 0)
    return out


def missions(db) -> list[dict]:
    out, waiting, costs = [], _waiting(db), _costs(db)
    for o in db.q("SELECT * FROM objectives ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 WHEN 'done' THEN 2 ELSE 3 END, id DESC"):
        s = _summary(db, "objective_id=?", o["id"])
        done, w = o["status"] == "done", waiting.get(("objectif", o["id"]), [])
        archived = o["status"] == "archived"
        out.append({"kind": "objectif", "id": o["id"], "title": _short(o["text"], 90), "text": o["text"], "activity": o["activity"],
                    "state": {"active": "En cours", "paused": "En pause", "done": "Terminée (agents)", "archived": "Archivée"}.get(o["status"], o["status"]),
                    "status": o["status"], "declared_done": done, "archived": archived, "waiting": w, **s,
                    "issues": [] if archived else _issues(done, o["status"] == "active", s, o["status"] == "paused", w),
                    "owner_check": o["owner_check"], "owner_note": o["owner_note"], "checked_at": o["checked_at"], "created_at": o["created_at"],
                    "pm": bool(o["project_manager"]), "pm_summary": o["pm_summary"]})
    for r in db.q("SELECT rowid AS n, * FROM sales WHERE stage IS NOT NULL AND (stage!='historique' OR owner_check='archived') "
                  "ORDER BY created_at DESC LIMIT 60"):
        s = _summary(db, "order_ref=?", r["n"])
        done, w = r["stage"] == "livree", waiting.get(("commande", r["n"]), [])
        archived = r["owner_check"] == "archived"
        late = not done and not archived and r["due_at"] and r["due_at"] < iso()
        st = "Archivée" if archived else {"a_livrer": "À livrer", "attente_client": "Attente client", "livree": "Livrée (agents)"}.get(r["stage"], r["stage"])
        issues = [] if archived else _issues(done, r["stage"] != "attente_client", s, False, w) + (["échéance dépassée"] if late else [])
        out.append({"kind": "commande", "id": r["n"], "title": f"Commande n°{r['n']} · {r['product'] or 'commande'}", "text": r["product"] or "",
                    "activity": "services", "state": st, "status": r["stage"], "declared_done": done, "archived": archived, "waiting": w, **s, "issues": issues,
                    "owner_check": r["owner_check"], "owner_note": r["owner_notes"], "checked_at": r["checked_at"], "created_at": r["created_at"],
                    "test": not r["livemode"], "pm": bool(r["project_manager"]), "pm_summary": None})
    for m in out:
        m["to_check"] = _to_check(m)
        m["cost_usd"] = round(costs.get((m["kind"], m["id"]), 0), 4)
    return out


def detail(db, kind: str, mid: int) -> dict:
    m = next((x for x in missions(db) if x["kind"] == kind and x["id"] == int(mid)), None)
    if not m:
        raise KeyError("Mission introuvable")
    col = "objective_id" if kind == "objectif" else "order_ref"
    m["tasks"] = [dict(r) for r in db.q(f"SELECT t.id, t.parent_id, t.title, t.agent, t.status, t.priority, t.result, t.created_by, "
                                        f"t.updated_at, t.attempts, (SELECT ROUND(COALESCE(SUM(usd),0), 4) FROM costs c WHERE c.task_id=t.id) AS cost_usd "
                                        f"FROM tasks t WHERE t.{col}=? ORDER BY t.id", (int(mid),))]
    for t in m["tasks"]:
        t["result"] = _short(t["result"] or "", 400)
    return m


def verify(cfg, db, kind: str, mid: int, verdict: str, note: str = "") -> str:
    """Le propriétaire confirme qu'une mission est vraiment terminée, ou la renvoie au PDG avec ce qui ne va pas."""
    m = detail(db, kind, mid)
    note = (note or "").strip()
    if verdict == "archive":
        return archive(db, kind, mid, m["title"], note)
    if verdict not in ("ok", "issue"):
        raise ValueError("Verdict inconnu")
    if verdict == "issue" and len(note) < 5:
        raise ValueError("Explique ce qui ne va pas : le PDG s'en servira pour reprendre la mission.")
    if kind == "objectif":
        if verdict == "ok":
            db.run("UPDATE objectives SET owner_check='ok', owner_note=?, checked_at=?, status='done', done_at=COALESCE(done_at, ?) WHERE id=?",
                   (note or None, iso(), iso(), mid))
        else:
            db.run("UPDATE objectives SET owner_check='issue', owner_note=?, checked_at=?, status='active', simple=NULL WHERE id=?", (note, iso(), mid))
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


def archive(db, kind: str, mid: int, title: str, note: str = "") -> str:
    """Le propriétaire classe une mission qu'il ne veut plus suivre (doublon, essai, abandon) : ses tâches en attente sont annulées."""
    if kind == "objectif":
        db.run("UPDATE objectives SET status='archived', owner_note=?, checked_at=? WHERE id=?", (note or None, iso(), mid))
        col = "objective_id"
    else:
        db.run("UPDATE sales SET owner_check='archived', checked_at=?, stage='historique' WHERE rowid=?", (iso(), mid))
        col = "order_ref"
    n = db.conn.execute(f"UPDATE tasks SET status='blocked', result='Annulée : mission archivée par le propriétaire' "
                        f"WHERE {col}=? AND status IN ('pending','blocked')", (mid,)).rowcount
    db.conn.commit()
    db.log("proprietaire", "mission", f"{title} : archivée{' — ' + note if note else ''} ({n} tâche(s) en attente annulée(s))")
    return f"Mission archivée ({n} tâche(s) en attente annulée(s))."


# ============================================================================ chefs de projet
PM = "chef_projet"


def mission_of(task) -> tuple[str, int] | None:
    if task is None:
        return None
    if task["order_ref"]:
        return ("commande", int(task["order_ref"]))
    if task["objective_id"]:
        return ("objectif", int(task["objective_id"]))
    return None


def has_pm(db, kind: str, mid: int) -> bool:
    if kind == "objectif":
        r = db.one("SELECT project_manager FROM objectives WHERE id=?", (mid,))
    else:
        r = db.one("SELECT project_manager FROM sales WHERE rowid=?", (mid,))
    return bool(r and r[0])


def mission_cost(db, kind: str, mid: int) -> float:
    col = "objective_id" if kind == "objectif" else "order_ref"
    return float(db.one(f"SELECT COALESCE(SUM(c.usd),0) FROM costs c JOIN tasks t ON t.id=c.task_id WHERE t.{col}=?", (mid,))[0])


def dossier(db, kind: str, mid: int) -> str:
    """Tout ce qu'un chef de projet doit savoir de sa mission, en un seul texte."""
    m = detail(db, kind, mid)
    head = f"{m['title']} — état : {m['state']} — avancement : {m['counts']['done']}/{m['total']} tâche(s) réussie(s)."
    if kind == "objectif":
        o = db.one("SELECT * FROM objectives WHERE id=?", (mid,))
        head += f"\nDemande du propriétaire : {o['text']}"
        if o["owner_check"] == "issue" and o["owner_note"]:
            head += f"\nREMARQUE DU PROPRIÉTAIRE (mission renvoyée, à traiter en priorité) : {o['owner_note']}"
    else:
        from .commerce import delivery_brief
        head += "\n" + delivery_brief(db, mid)
    if m["issues"]:
        head += "\nAnomalies : " + " ; ".join(m["issues"])
    if m["waiting"]:
        head += f"\nEn attente de validation du propriétaire : #{', #'.join(map(str, m['waiting']))} (ne rien relancer en double)."
    lines = []
    for t in m["tasks"][-25:]:
        lines.append(f"- #{t['id']} [{t['status']}] {t['agent']} : {t['title']}" + (f"\n    → {t['result'][:300]}" if t["result"] else ""))
    return head + "\n\nTâches de la mission :\n" + ("\n".join(lines) or "(aucune)")


def assign_pm(db, kind: str, mid: int, brief: str = "", by: str = "pdg") -> int | None:
    """Confie une mission à un chef de projet et lui crée sa tâche de lancement."""
    if kind == "objectif":
        db.run("UPDATE objectives SET project_manager=? WHERE id=?", (PM, mid))
    else:
        db.run("UPDATE sales SET project_manager=? WHERE rowid=?", (PM, mid))
    title = detail(db, kind, mid)["title"]
    db.log(by, "mission", f"{title} : confiée à un chef de projet")
    return db.add_task(f"Piloter : {title}"[:120], PM,
                       (f"Consignes de lancement : {brief}\n\n" if brief else "") +
                       "Tu es le chef de projet de cette mission. Lance mission_status, établis le plan, délègue les premières "
                       "étapes et termine ta tâche : tu seras réveillé à la fin de chaque étape.",
                       priority=2, created_by=by, objective_id=mid if kind == "objectif" else None,
                       order_ref=mid if kind == "commande" else None)


def wake_pm(db, task) -> int | None:
    """Une étape de mission vient de se terminer : le chef de projet est réveillé (une seule tâche de suivi en attente)."""
    if task is None or task["agent"] == PM:
        return None
    m = mission_of(task)
    if not m or not has_pm(db, *m):
        return None
    col = "objective_id" if m[0] == "objectif" else "order_ref"
    t = db.task(task["id"])
    line = f"- #{t['id']} {t['agent']} « {t['title']} » → {t['status']}"
    pending = db.one(f"SELECT id, description FROM tasks WHERE agent=? AND {col}=? AND status='pending' ORDER BY id LIMIT 1", (PM, m[1]))
    if pending:                                   # déjà réveillé : on complète sa liste au lieu d'empiler les tâches
        db.run("UPDATE tasks SET description=? WHERE id=?", (pending["description"] + "\n" + line, pending["id"]))
        return pending["id"]
    return db.add_task(f"Suivi : étape terminée ({t['agent']})"[:120], PM,
                       "Étape(s) terminée(s) dans ta mission :\n" + line +
                       "\n\nRelis le résultat (get_task_result), puis lance l'étape suivante, fais corriger, ou clôture.",
                       priority=2, created_by="systeme", objective_id=m[1] if m[0] == "objectif" else None,
                       order_ref=m[1] if m[0] == "commande" else None)


def over_cap(cfg, db, task) -> str | None:
    """Plafond de dépense d'une mission pilotée : au-delà, ses tâches attendent la décision du propriétaire."""
    m = mission_of(task)
    if not m or not has_pm(db, *m):
        return None
    cap = float(cfg.get("missions", "max_usd_per_mission", default=3.0) or 0)
    spent = mission_cost(db, *m)
    if cap and spent >= cap:
        return f"plafond de la mission atteint ({spent:.2f} $ sur {cap:.2f} $)"
    return None


def close(db, kind: str, mid: int, summary: str, by: str = PM) -> str:
    m = detail(db, kind, mid)
    open_ = [t for t in m["tasks"] if t["status"] in ("pending", "running") and t["agent"] != PM]
    if open_:
        return "Clôture refusée : des tâches sont encore ouvertes (" + ", ".join(f"#{t['id']}" for t in open_[:6]) + "). Attends-les ou annule-les."
    if m["waiting"]:
        return f"Clôture refusée : la validation #{m['waiting'][0]} attend encore le propriétaire."
    if kind == "commande":
        if m["status"] != "livree":
            return "Une commande se clôt par sa livraison : fais livrer le client (deliver_order via le support)."
        db.run("UPDATE sales SET stage_note=? WHERE rowid=?", (summary[:300], mid))
    else:
        db.run("UPDATE objectives SET status='done', done_at=?, pm_summary=?, owner_check=NULL WHERE id=?", (iso(), summary[:2000], mid))
    db.log(by, "mission", f"{m['title']} : clôturée par le chef de projet — {summary[:200]}")
    return "Mission clôturée : elle passe dans « À vérifier » pour le propriétaire."


def relaunch_if_stalled(db, task) -> int | None:
    """Après une tâche du chef de projet : relance si la mission s'est arrêtée (voir relaunch_mission)."""
    if task is None or task["agent"] != PM:
        return None
    m = mission_of(task)
    if not m or not has_pm(db, *m):
        return None
    return relaunch_mission(db, *m)


def relaunch_mission(db, kind: str, mid: int, request: str = "") -> int | None:
    """Mission pilotée ni close ni livrée, sans rien en cours ni en attente de validation : le chef de projet est relancé.
    Après 2 relances (comptées depuis la dernière intervention du propriétaire), le PDG arbitre ; si cela ne
    suffit pas, une décision est demandée au propriétaire. `request` : consigne du PDG ou du propriétaire."""
    m = (kind, int(mid))
    d = detail(db, *m)
    if d["archived"] or (d["kind"] == "objectif" and d["status"] in ("done", "paused")) or (d["kind"] == "commande" and d["status"] == "livree"):
        return None
    if d["counts"]["pending"] or d["counts"]["running"]:
        return None
    if d["waiting"]:
        stuck = [a["id"] for a in db.q("SELECT id, reason FROM approvals WHERE status='pending' AND action='decision'")
                 if a["id"] in d["waiting"] and (a["reason"] or "").startswith("Mission bloquée")]
        if not request or set(stuck) != set(d["waiting"]):
            return None
        for aid in stuck:                   # une consigne explicite remplace la demande de décision « mission bloquée »
            db.set_approval(aid, status="approved", decided_at=iso(), result=f"Remplacée par la consigne : {request}"[:500])
    col = "objective_id" if m[0] == "objectif" else "order_ref"
    # On compte depuis la dernière intervention du propriétaire : les relances et arbitrages déjà faits, même si
    # d'autres agents ont travaillé entre-temps (sinon une mission qui tourne en rond n'est jamais escaladée).
    base = db.one(f"SELECT COALESCE(MAX(id), 0) FROM tasks WHERE {col}=? AND created_by='proprietaire'", (m[1],))[0]
    rows = db.q(f"SELECT id, agent, title FROM tasks WHERE {col}=? AND id>? ORDER BY id", (m[1], base))
    arbs = [r["id"] for r in rows if r["agent"] == "pdg" and r["title"].startswith("Arbitrage")]
    since = arbs[-1] if arbs else 0
    stalls = sum(1 for r in rows if r["id"] > since and r["agent"] == PM and r["title"].startswith("Relance :"))
    last_is_arb = bool(rows) and rows[-1]["id"] == since and since > 0
    kw = {"objective_id": m[1] if m[0] == "objectif" else None, "order_ref": m[1] if m[0] == "commande" else None}
    if not request and arbs and (last_is_arb or stalls >= 2):
        # même l'arbitrage du PDG n'a rien débloqué : on s'arrête et on demande au propriétaire, une seule fois
        key = f"mission_stuck_{m[0]}_{m[1]}_{base}"
        if not db.kv_get(key):
            db.kv_set(key, iso())
            last_pm = db.one(f"SELECT id, result FROM tasks WHERE {col}=? AND agent=? ORDER BY id DESC LIMIT 1", (m[1], PM))
            db.add_approval(PM, last_pm["id"] if last_pm else None, "decision",
                            {"question": f"Mission bloquée : {d['title']}"[:200],
                             "context": ("Le chef de projet a été relancé et le PDG a arbitré, sans progrès. Dernier point du "
                                         "chef de projet :\n" + ((last_pm["result"] or "")[:1500] if last_pm else "—") +
                                         "\n\nRéponds avec une consigne (elle relance la mission) ou refuse pour l'abandonner.")},
                            f"Mission bloquée : {d['title']}"[:200])
            db.log("systeme", "mission", f"{d['title']} : bloquée malgré les relances et l'arbitrage du PDG — ta décision est nécessaire")
        return None
    if stalls >= 2 and not request:         # une consigne explicite (PDG, propriétaire) relance toujours le chef de projet
        db.log("systeme", "mission", f"{d['title']} : à l'arrêt malgré 2 relances du chef de projet → arbitrage du PDG")
        return db.add_task(f"Arbitrage : mission à l'arrêt — {d['title']}"[:120], "pdg",
                           "Le chef de projet a été relancé deux fois sans faire avancer la mission. Lance mission_status, "
                           "identifie le blocage (étape manquante, avis bloquant, agent qui échoue) et décide : relancer le "
                           "chef de projet avec une consigne précise (assign_project_manager avec un brief), changer "
                           "d'approche, clôturer, ou demander une décision au propriétaire (request_owner_decision). "
                           "Un avis juridique qui ajoute des conditions non imposées par la loi n'est pas un blocage : "
                           "tranche toi-même.",
                           priority=1, created_by="systeme", **kw)
    return db.add_task(f"Relance : la mission n'avance plus"[:120], PM,
                       (f"CONSIGNE DE RELANCE : {request}\n\n" if request else "") +
                       "Toutes les tâches de ta mission sont terminées mais elle n'est ni close ni livrée, et rien n'attend le "
                       "propriétaire. Lance mission_status puis : soit tu lances l'étape qui manque pour atteindre le résultat "
                       "demandé, soit tu clôtures (close_mission) si c'est réellement terminé. Si une étape a échoué ou n'a "
                       "rien produit, refais-la faire avec une consigne plus précise.",
                       priority=2, created_by="systeme", **kw)
