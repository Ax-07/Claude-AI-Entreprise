"""Remise à zéro opérationnelle : repartir sur une base propre sans perdre ce qui a de la valeur.

Supprimé : missions (objectifs), tâches, journal d'activité, validations, discussions avec le PDG, commandes d'essai
(sans paiement réel) ; les fichiers de travail sont déplacés hors de portée des agents.
Conservé : configuration, catalogue d'offres, activités, ventes réelles, coûts (budget et analyses), emails reçus,
bibliothèque de recherches, mémoire du propriétaire (et celle des agents, sauf option).
Une sauvegarde complète est faite avant toute modification.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


def fresh_start(cfg, db, agent_memory: bool = False) -> dict:
    from .backup import make_backup
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    saved = make_backup(cfg, db, f"{stamp}_avant-remise-a-zero")
    out = {"backup": str(saved)}
    counts = lambda t, w="1=1": db.one(f"SELECT COUNT(*) FROM {t} WHERE {w}")[0]
    out["tasks"], out["objectives"], out["approvals"] = counts("tasks"), counts("objectives"), counts("approvals")
    out["test_orders"] = counts("sales", "livemode=0")
    out["agent_memories"] = counts("memory", "agent!='proprietaire'") if agent_memory else 0
    with db.conn:
        for t in ("tasks", "objectives", "approvals", "events", "chat", "chat_threads"):
            db.conn.execute(f"DELETE FROM {t}")
        db.conn.execute("DELETE FROM sales WHERE livemode=0")              # essais : aucune valeur comptable
        db.conn.execute("UPDATE inbox SET task_id=NULL")
        db.conn.execute("DELETE FROM kv WHERE k LIKE 'order_%' OR k IN ('security_review_day')")
        if agent_memory:                                                   # souvenirs des agents (pas ceux du propriétaire)
            db.conn.execute("DELETE FROM memory WHERE agent!='proprietaire'")
    ws = Path(cfg.workspace)
    moved = 0
    if ws.exists() and any(ws.iterdir()):
        dest = Path(cfg.db_path).parent / "archives" / f"{stamp}_espace-de-travail"
        dest.mkdir(parents=True, exist_ok=True)
        for item in list(ws.iterdir()):
            shutil.move(str(item), str(dest / item.name))
            moved += 1
        out["workspace_archive"] = str(dest)
    out["workspace_items"] = moved
    db.log("proprietaire", "systeme", f"Remise à zéro : {out['tasks']} tâche(s), {out['objectives']} mission(s), "
           f"{out['test_orders']} commande(s) d'essai supprimées ; sauvegarde {saved.name}")
    return out
