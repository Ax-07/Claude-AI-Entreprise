"""Contrôle des dépenses, domaine de la Trésorerie : écarts par rapport à l'habitude, sans appel au modèle."""
from __future__ import annotations

from datetime import timedelta

from .db import now


def spending_report(cfg, db) -> dict:
    today = now().strftime("%Y-%m-%d")
    week = [(now() - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(1, 8)]
    marks = ",".join("?" * 7)
    spent = db.one("SELECT COALESCE(SUM(usd),0) FROM costs WHERE day=?", (today,))[0]
    avg = db.one(f"SELECT COALESCE(SUM(usd),0) FROM costs WHERE day IN ({marks})", week)[0] / 7
    daily_cap = float(cfg["budget"].get("daily_usd", 0) or 0)
    anomalies, by_agent = [], []
    if spent > 0.2 and spent > 3 * max(avg, 0.01):
        anomalies.append(f"Dépense du jour {spent:.3f} $, plus de 3 fois la moyenne des 7 derniers jours ({avg:.3f} $/jour).")
    for r in db.q("SELECT agent, SUM(usd) s, COUNT(DISTINCT task_id) n FROM costs WHERE day=? GROUP BY agent ORDER BY s DESC", (today,)):
        a7 = db.one(f"SELECT COALESCE(SUM(usd),0) FROM costs WHERE agent=? AND day IN ({marks})", (r["agent"], *week))[0] / 7
        by_agent.append(f"{r['agent']} : {r['s']:.3f} $ ({r['n']} tâche(s)) — moyenne {a7:.3f} $/jour")
        if r["s"] > 0.15 and r["s"] > 3 * max(a7, 0.01):
            anomalies.append(f"L'agent {r['agent']} a dépensé {r['s']:.3f} $ aujourd'hui, plus de 3 fois sa moyenne ({a7:.3f} $/jour).")
    unknown = db.q("SELECT DISTINCT agent FROM costs WHERE day=? AND agent NOT IN (%s)" % ",".join("?" * len(_agents())), (today, *_agents()))
    for r in unknown:
        anomalies.append(f"Dépense attribuée à un agent inconnu : {r['agent']}.")
    return {"today": round(spent, 4), "avg7": round(avg, 4), "daily_cap": daily_cap, "by_agent": by_agent, "anomalies": anomalies}


def _agents() -> list[str]:
    from .agents import ROLES
    return list(ROLES) + ["brave-search", "systeme", "proprietaire"]


def report_text(r: dict) -> str:
    lines = [f"Dépense du jour : {r['today']:.3f} $ (moyenne 7 jours : {r['avg7']:.3f} $/jour"
             + (f", plafond {r['daily_cap']:.2f} $" if r["daily_cap"] else "") + ")."]
    lines += ["Par agent :"] + [f"- {x}" for x in r["by_agent"]] if r["by_agent"] else ["Aucune dépense aujourd'hui."]
    lines += ["Anomalies :"] + ([f"- {a}" for a in r["anomalies"]] or ["- Aucune."])
    return "\n".join(lines)
