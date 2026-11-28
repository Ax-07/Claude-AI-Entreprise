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


def fresh_start(cfg, db, agent_memory: bool = False, all_prospects: bool = False) -> dict:
    from .backup import make_backup
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    saved = make_backup(cfg, db, f"{stamp}_avant-remise-a-zero")
    out = {"backup": str(saved)}
    counts = lambda t, w="1=1": db.one(f"SELECT COUNT(*) FROM {t} WHERE {w}")[0]
    out["tasks"], out["objectives"], out["approvals"] = counts("tasks"), counts("objectives"), counts("approvals")
    out["test_orders"] = counts("sales", "livemode=0")
    out["agent_memories"] = counts("memory", "agent!='proprietaire'") if agent_memory else 0
    # prospects : ceux du mode test (tes propres adresses) sont effacés pour pouvoir refaire les essais ; les autres sont
    # gardés (historique de prospection) sauf --prospects ; les oppositions « STOP » sont TOUJOURS gardées.
    from .config import test_recipients
    test_addrs = [e.lower() for e in (test_recipients(cfg) or [])]
    if all_prospects:
        where_p = "status!='stop'"
    elif test_addrs:
        where_p = f"status!='stop' AND email IN ({','.join(repr(e) for e in test_addrs)})"
    else:
        where_p = "0"
    out["prospects"] = counts("prospects", where_p)
    with db.conn:
        for t in ("tasks", "objectives", "approvals", "events", "chat", "chat_threads"):
            db.conn.execute(f"DELETE FROM {t}")
        db.conn.execute("DELETE FROM sales WHERE livemode=0")              # essais : aucune valeur comptable
        db.conn.execute("UPDATE inbox SET task_id=NULL")
        db.conn.execute(f"DELETE FROM prospects WHERE {where_p}")
        db.conn.execute("DELETE FROM kv WHERE k LIKE 'order_%' OR k LIKE 'mission_stuck_%' OR k LIKE 'legal_round_task_%' "
                        "OR k IN ('security_review_day')")
        # numérotation repartant de 1 pour les missions, validations et discussions (pas pour les tâches : les coûts
        # historiques y sont rattachés par numéro)
        db.conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('objectives','approvals','events','chat','chat_threads')")
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


def delete_mission(cfg, db, kind: str, mid: int) -> dict:
    """Supprime une mission et tout ce qui s'y rattache (tâches, validations, journal), après une sauvegarde.
    Conservés : coûts (le budget reste exact), bibliothèque de recherches, mémoire des agents, fichiers de travail.
    Refusé si un agent travaille dessus en ce moment, et pour une commande réellement payée."""
    from .backup import make_backup
    if kind not in ("objectif", "commande"):
        raise ValueError("Type de mission inconnu")
    mid = int(mid)
    col = "objective_id" if kind == "objectif" else "order_ref"
    if kind == "objectif":
        row = db.one("SELECT id, text FROM objectives WHERE id=?", (mid,))
        if not row:
            raise KeyError(f"Mission #{mid} introuvable")
        title = f"Mission #{mid} · {row['text'][:80]}"
    else:
        row = db.one("SELECT rowid AS n, * FROM sales WHERE rowid=?", (mid,))
        if not row:
            raise KeyError(f"Commande n°{mid} introuvable")
        if row["livemode"]:
            raise ValueError("Commande réellement payée : elle ne peut pas être supprimée (archive-la).")
        title = f"Commande d'essai n°{mid}"
    if db.one(f"SELECT 1 FROM tasks WHERE {col}=? AND status='running'", (mid,)):
        raise ValueError("Un agent travaille sur cette mission en ce moment : attends la fin de sa tâche (ou arrête le "
                         "moteur), puis recommence.")
    ids = [r[0] for r in db.q(f"SELECT id FROM tasks WHERE {col}=?", (mid,))]
    saved = make_backup(cfg, db, datetime.now().strftime("%Y-%m-%d_%H%M%S") + f"_avant-suppression-{kind}-{mid}")
    marks = ",".join("?" * len(ids)) or "NULL"
    with db.conn:
        n_appr = db.conn.execute(f"DELETE FROM approvals WHERE task_id IN ({marks})", ids).rowcount
        if kind == "commande":
            n_appr += db.conn.execute("DELETE FROM approvals WHERE json_extract(payload, '$.order')=?", (mid,)).rowcount
        db.conn.execute(f"DELETE FROM events WHERE task_id IN ({marks})", ids)
        db.conn.execute(f"UPDATE inbox SET task_id=NULL WHERE task_id IN ({marks})", ids)
        db.conn.execute(f"DELETE FROM tasks WHERE {col}=?", (mid,))
        db.conn.execute("DELETE FROM kv WHERE k LIKE ?", (f"mission_stuck_{kind}_{mid}_%",))
        if kind == "objectif":
            db.conn.execute("DELETE FROM objectives WHERE id=?", (mid,))
        else:
            db.conn.execute("DELETE FROM sales WHERE rowid=?", (mid,))
            db.conn.execute("DELETE FROM kv WHERE k IN (?, ?)", (f"order_stuck_{mid}", f"order_late_{mid}"))
    db.log("proprietaire", "mission", f"{title} supprimée ({len(ids)} tâche(s), {n_appr} validation(s)) ; sauvegarde {saved.name}")
    return {"title": title, "tasks": len(ids), "approvals": n_appr, "backup": str(saved)}
