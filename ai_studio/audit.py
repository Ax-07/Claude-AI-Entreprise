"""Audit RH des agents : dossier chiffré de la semaine, calculé sans appel d'API à partir du journal, des tâches,
des coûts et des validations. L'auditeur RH (indépendant du PDG) l'analyse, recommande au propriétaire et peut
proposer des consignes permanentes pour un agent — appliquées seulement après validation du propriétaire."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta

from .db import iso, now


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower())[:40].strip()


def dossier(cfg, db, days: int = 7) -> dict:
    since = iso(now() - timedelta(days=days))
    from .agents import ROLES
    A = defaultdict(lambda: defaultdict(float))
    for t in db.q("SELECT agent, status, attempts, cost_usd FROM tasks WHERE updated_at>=?", (since,)):
        a = A[t["agent"]]
        a["tasks"] += 1
        a[t["status"]] += 1
        a["retries"] += t["attempts"] > 1
        a["cost"] += t["cost_usd"] or 0
    for e in db.q("SELECT agent, kind, message FROM events WHERE ts>=? AND kind IN ('tool','error')", (since,)):
        a, m = A[e["agent"]], e["message"]
        if e["kind"] == "error" and "sans résultat" in m:
            a["empty"] += 1
            continue
        if e["kind"] != "tool":
            continue
        name = m.split("(", 1)[0]
        a["tool_calls"] += 1
        out = m.split(" → ", 1)[1] if " → " in m else ""
        if out.startswith(("Refusé", "Erreur", "Livraison refusée", "Email non conforme")):
            a["tool_refusals"] += 1
            if name == "delegate_task":
                a["delegation_refused"] += 1
        if name == "delegate_task":
            a["delegations"] += 1
        elif name in ("recall", "search_library"):
            a["memory_checks"] += 1
        elif name == "deliver_order" and out.startswith("Livraison refusée"):
            a["qa_blocked"] += 1
    for c in db.q("SELECT agent, COALESCE(SUM(web_searches),0) AS w FROM costs WHERE ts>=? GROUP BY agent", (since,)):
        A[c["agent"]]["web_searches"] += c["w"]
    for ap in db.q("SELECT agent, action, status, result FROM approvals WHERE created_at>=?", (since,)):
        a = A[ap["agent"]]
        a["submitted"] += 1
        if ap["status"] in ("approved", "executed", "bounced"):
            a["accepted"] += 1
        elif ap["status"] == "rejected":
            if (ap["result"] or "").startswith("MODIFICATION DEMANDÉE"):
                a["sent_back"] += 1
            elif not (ap["result"] or "").startswith("Remplacée"):
                a["rejected"] += 1
    for e in db.q("SELECT message FROM events WHERE ts>=? AND kind='decision' AND message LIKE '%modifiée avant validation%'", (since,)):
        m = re.search(r"#(\d+)", e["message"])
        ap = db.approval(int(m.group(1))) if m else None
        if ap:
            A[ap["agent"]]["edited_by_owner"] += 1
    # doublons : même agent, même mission, titres presque identiques
    dup = defaultdict(int)
    for r in db.q("SELECT agent, COALESCE(objective_id, -order_ref) AS m, title FROM tasks WHERE created_at>=? AND "
                  "(objective_id IS NOT NULL OR order_ref IS NOT NULL) AND title NOT LIKE 'Relance%' AND title NOT LIKE 'Suivi :%' "
                  "AND title NOT LIKE 'Point de mission%'", (since,)):
        dup[(r["agent"], r["m"], _norm(r["title"]))] += 1
    for (agent, _, _), n in dup.items():
        if n > 1:
            A[agent]["duplicates"] += n - 1
    missions = {
        "relaunches": db.one("SELECT COUNT(*) FROM tasks WHERE created_at>=? AND title LIKE 'Relance :%'", (since,))[0],
        "arbitrations": db.one("SELECT COUNT(*) FROM tasks WHERE created_at>=? AND title LIKE 'Arbitrage :%'", (since,))[0],
        "blocked_decisions": db.one("SELECT COUNT(*) FROM approvals WHERE created_at>=? AND reason LIKE 'Mission bloquée%'", (since,))[0],
        "sent_back_by_owner": db.one("SELECT COUNT(*) FROM objectives WHERE owner_check='issue' AND checked_at>=?", (since,))[0]
                              + db.one("SELECT COUNT(*) FROM sales WHERE owner_check='issue' AND checked_at>=?", (since,))[0],
        "unsatisfied_clients": db.one("SELECT COUNT(*) FROM sales WHERE satisfied=0 AND delivered_at>=?", (since,))[0],
    }
    agents = []
    for name, a in sorted(A.items(), key=lambda kv: -kv[1]["cost"]):
        if name not in ROLES or not a["tasks"] and not a["submitted"]:
            continue
        done, fail = a["done"], a["failed"] + a["blocked"]
        agents.append({"agent": name, "title": ROLES[name].title, "tasks": int(a["tasks"]), "done": int(done), "failed": int(fail),
                       "success_pct": round(100 * done / (done + fail), 1) if done + fail else None,
                       "empty": int(a["empty"]), "retries": int(a["retries"]), "cost_usd": round(a["cost"], 4),
                       "cost_per_task": round(a["cost"] / a["tasks"], 4) if a["tasks"] else None,
                       "tool_calls": int(a["tool_calls"]), "tool_refusals": int(a["tool_refusals"]),
                       "delegations": int(a["delegations"]), "delegation_refused": int(a["delegation_refused"]),
                       "memory_checks": int(a["memory_checks"]), "web_searches": int(a["web_searches"]),
                       "duplicates": int(a["duplicates"]), "qa_blocked": int(a["qa_blocked"]),
                       "submitted": int(a["submitted"]), "accepted": int(a["accepted"]), "rejected": int(a["rejected"]),
                       "sent_back": int(a["sent_back"]), "edited_by_owner": int(a["edited_by_owner"])})
    rules = [dict(r) for r in db.q("SELECT id, agent, rule, created_at FROM agent_rules WHERE active=1 ORDER BY agent, id")]
    return {"days": days, "since": since[:10], "until": iso()[:10], "agents": agents, "missions": missions, "rules": rules}


def signals(a: dict) -> list[str]:
    """Points d'attention objectifs, pour guider l'analyse (seuils simples, pas un jugement)."""
    out = []
    if a["success_pct"] is not None and a["success_pct"] < 80 and a["done"] + a["failed"] >= 3:
        out.append(f"taux de réussite {a['success_pct']:.0f} %")
    if a["empty"]:
        out.append(f"{a['empty']} fin(s) de tâche sans résultat")
    if a["duplicates"]:
        out.append(f"{a['duplicates']} tâche(s) en double dans une même mission")
    if a["delegation_refused"]:
        out.append(f"{a['delegation_refused']} délégation(s) refusée(s) (chaîne de commandement)")
    if a["web_searches"] >= 3 and a["memory_checks"] == 0:
        out.append(f"{a['web_searches']} recherches web sans consulter mémoire ni bibliothèque")
    if a["tool_calls"] and a["tool_refusals"] / a["tool_calls"] > 0.25 and a["tool_refusals"] >= 3:
        out.append(f"{a['tool_refusals']} appels d'outil refusés sur {a['tool_calls']}")
    if a["qa_blocked"]:
        out.append(f"{a['qa_blocked']} livraison(s) bloquée(s) par le contrôle qualité")
    if a["submitted"] and (a["rejected"] + a["sent_back"] + a["edited_by_owner"]) / a["submitted"] >= 0.5 and a["submitted"] >= 2:
        out.append(f"{a['rejected']} refus, {a['sent_back']} renvois et {a['edited_by_owner']} corrections du propriétaire sur {a['submitted']} demandes")
    return out


def to_markdown(d: dict) -> str:
    pc = lambda v: "—" if v is None else f"{v:.0f} %"
    L = [f"# Audit des agents — du {d['since']} au {d['until']}", "",
         "Dossier calculé automatiquement (journal, tâches, coûts, validations) : chiffres exacts, sans interprétation.", "",
         "| Agent | Tâches | Réussite | Sans résultat | Coût | Coût/tâche | Outils refusés | Délégations (refusées) | Mémoire/bibliothèque | Recherches web | Doublons | Demandes (acceptées / refusées / renvoyées / corrigées) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in d["agents"]:
        cpt = "—" if a["cost_per_task"] is None else f"{a['cost_per_task']:.3f} $"
        L.append(f"| {a['agent']} | {a['tasks']} | {pc(a['success_pct'])} | {a['empty']} | {a['cost_usd']:.3f} $ | "
                 f"{cpt} | {a['tool_refusals']}/{a['tool_calls']} | "
                 f"{a['delegations']} ({a['delegation_refused']}) | {a['memory_checks']} | {a['web_searches']} | {a['duplicates']} | "
                 f"{a['submitted']} ({a['accepted']} / {a['rejected']} / {a['sent_back']} / {a['edited_by_owner']}) |")
    if not d["agents"]:
        L.append("| (aucune activité) |  |  |  |  |  |  |  |  |  |  |  |")
    m = d["missions"]
    L += ["", "## Pilotage des missions",
          f"- Relances de chefs de projet : {m['relaunches']} · arbitrages du PDG : {m['arbitrations']} · missions bloquées remontées au propriétaire : {m['blocked_decisions']}",
          f"- Missions renvoyées par le propriétaire (« pas terminée ») : {m['sent_back_by_owner']} · clients insatisfaits : {m['unsatisfied_clients']}",
          "", "## Points d'attention (seuils automatiques)"]
    sig = [(a["agent"], s) for a in d["agents"] for s in signals(a)]
    L += [f"- {ag} : {s}" for ag, s in sig] or ["- Aucun."]
    L += ["", "## Consignes permanentes en vigueur"]
    L += [f"- #{r['id']} {r['agent']} : {r['rule']}" for r in d["rules"]] or ["- Aucune."]
    return "\n".join(L) + "\n"


def iso_week(d=None) -> str:
    y, w, _ = (d or now()).isocalendar()
    return f"{y}-S{w:02d}"


def run_weekly(cfg, db) -> str | None:
    """Le lundi (ou au premier cycle de la semaine) : dossier de la semaine écoulée + revue de l'auditeur RH."""
    if not cfg.get("audit", "weekly", default=True):
        return None
    week = iso_week()
    if db.kv_get("audit_week") == week:
        return None
    db.kv_set("audit_week", week)
    d = dossier(cfg, db, 7)
    if sum(a["tasks"] for a in d["agents"]) < 5:
        return None                                 # trop peu d'activité pour un audit utile
    path = f"rapports/audit-agents-{iso_week(now() - timedelta(days=7))}.md"
    f = cfg.workspace / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(to_markdown(d), encoding="utf-8")
    tid = db.add_task(f"Audit hebdomadaire des agents", "rh",
                      f"Le dossier chiffré de la semaine est prêt : {path}. Lis-le (read_file ; agent_audit pour une autre "
                      "période), puis ajoute à la fin du fichier une section « Analyse » : ce qui fonctionne, les 3 problèmes "
                      "les plus coûteux (en argent, en temps du propriétaire ou en qualité), et AU PLUS 3 recommandations au "
                      "propriétaire, chacune appuyée sur un chiffre du dossier. Si un comportement d'agent se répète, propose "
                      "une consigne permanente (propose_agent_rule) : courte, précise, vérifiable. N'en propose pas sans "
                      "preuve chiffrée, et vérifie qu'elle ne double pas une consigne existante. Termine par un résultat "
                      "de 5 lignes au plus pour le propriétaire.",
                      priority=3, created_by="systeme")
    db.log("rh", "rapport", f"Dossier d'audit de la semaine écrit dans {path} (revue : tâche #{tid})", tid)
    return path


# ------------------------------------------------------------------ consignes permanentes
def rules_for(db, agent: str) -> list[str]:
    return [r["rule"] for r in db.q("SELECT rule FROM agent_rules WHERE agent=? AND active=1 ORDER BY id", (agent,))]


def rules_text(db, agent: str) -> str:
    rules = rules_for(db, agent)
    return ("\n\nConsignes permanentes validées par le propriétaire (elles s'imposent) :\n" + "\n".join(f"- {r}" for r in rules)) if rules else ""


def apply_rule(db, payload: dict, approval_id: int) -> str:
    rid = db.run("INSERT INTO agent_rules(agent, rule, reason, approval_id, created_at) VALUES (?,?,?,?,?)",
                 (payload["agent"], payload["rule"], payload.get("evidence", ""), approval_id, iso()))
    db.log("proprietaire", "rh", f"Consigne permanente #{rid} ajoutée pour {payload['agent']} : {payload['rule'][:100]}")
    return f"consigne #{rid} ajoutée à {payload['agent']}"


def remove_rule(db, rid: int) -> str:
    r = db.one("SELECT * FROM agent_rules WHERE id=? AND active=1", (int(rid),))
    if not r:
        raise KeyError("Consigne introuvable ou déjà retirée")
    db.run("UPDATE agent_rules SET active=0, removed_at=? WHERE id=?", (iso(), r["id"]))
    db.log("proprietaire", "rh", f"Consigne permanente #{r['id']} retirée ({r['agent']})")
    return f"Consigne #{r['id']} retirée pour {r['agent']}."
