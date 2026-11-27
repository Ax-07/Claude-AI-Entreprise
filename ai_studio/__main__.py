"""Ligne de commande : python -m ai_studio <commande>

Commandes principales :
  init                         crée config.json et .env à partir des modèles
  objectif "texte"             fixe un objectif à l'entreprise (le PDG s'en charge)
  tache "titre" -a agent       confie directement une tâche à un agent (option -d description, -p priorité)
  run                          démarre le moteur en continu (24 h/24)
  run --once                   exécute un seul cycle puis s'arrête (pour tester)
  status                       vue d'ensemble : objectifs, tâches, budget, validations en attente
  validations                  liste les actions qui attendent ta validation
  valider ID [-n "note"]       valide une action (l'email part, le lien de paiement est créé…)
  refuser ID [-n "raison"]     refuse une action
  taches [-s statut]           liste les tâches
  voir ID                      affiche le résultat complet d'une tâche
  journal [-n 30]              derniers événements
  couts                        dépenses API par jour, par agent et par modèle
  agents                       liste l'équipe et ses outils
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys

from .config import ROOT, load_config
from .db import DB


def _out(s=""):
    try:
        print(s)
    except UnicodeEncodeError:
        print(str(s).encode("ascii", "replace").decode())


def cmd_init(cfg, db, a):
    for src, dst in (("config.example.json", "config.json"), (".env.example", ".env")):
        s, d = cfg.root / src, cfg.root / dst
        if d.exists():
            _out(f"{dst} existe déjà — non modifié.")
        elif s.exists():
            shutil.copy(s, d); _out(f"{dst} créé. Ouvre-le pour le compléter.")
    _out(f"Base de données : {cfg.db_path}")


def cmd_objectif(cfg, db, a):
    oid = db.add_objective(a.texte)
    db.log("proprietaire", "objective", f"Nouvel objectif #{oid} : {a.texte}")
    _out(f"Objectif #{oid} enregistré. Le PDG le prendra en charge au prochain cycle.")


def cmd_tache(cfg, db, a):
    from .agents import ROLES
    if a.agent not in ROLES:
        _out(f"Agent inconnu. Choix : {', '.join(ROLES)}"); return
    tid = db.add_task(a.titre, a.agent, a.description or "", a.priorite)
    _out(f"Tâche #{tid} confiée à {a.agent}.")


def _studio(cfg, db):
    from .llm import AnthropicClient
    from .orchestrator import Studio
    llm = AnthropicClient(timeout=int(cfg["runtime"]["request_timeout_seconds"]))
    return Studio(cfg, db, llm)


def cmd_run(cfg, db, a):
    st = _studio(cfg, db)
    if a.once:
        st.recover()
        s = st.cycle()
        _out(f"Cycle terminé : {s['ran']} tâche(s) exécutée(s), {s['reviews']} revue(s) planifiée(s)."
             + (f" Pause : {s['paused']}" if s["paused"] else ""))
        _out(f"Dépense du jour : {db.spent('day'):.4f} $")
    else:
        st.run_forever(_out)


def cmd_status(cfg, db, a):
    b = cfg["budget"]
    _out(f"== {cfg['company']['name']} ==")
    _out(f"Budget : jour {db.spent('day'):.4f} / {b['daily_usd']} $ — mois {db.spent('month'):.4f} / {b['monthly_usd']} $"
         f" — recherches web {db.searches_today()} / {b['web_searches_per_day']}")
    _out("\nObjectifs :")
    for o in db.q("SELECT * FROM objectives ORDER BY id"):
        _out(f"  #{o['id']} [{o['status']}] {o['text']}")
    _out("\nTâches : " + ", ".join(f"{r['status']}={r['n']}" for r in db.q("SELECT status, COUNT(*) n FROM tasks GROUP BY status")))
    for t in db.q("SELECT * FROM tasks WHERE status IN ('pending','running') ORDER BY priority, id LIMIT 10"):
        _out(f"  #{t['id']} [{t['status']}] {t['agent']} : {t['title']}")
    n = len(db.pending_approvals())
    _out(f"\nValidations en attente : {n}" + ("  → python -m ai_studio validations" if n else ""))


def cmd_validations(cfg, db, a):
    rows = db.pending_approvals()
    if not rows:
        _out("Aucune action en attente."); return
    for r in rows:
        p = json.loads(r["payload"])
        _out(f"\n#{r['id']} — {r['action']} — demandé par {r['agent']} (tâche #{r['task_id']})")
        _out(f"  Raison : {r['reason']}")
        for k, v in p.items():
            _out(f"  {k} : {v}")
    _out("\nValider : python -m ai_studio valider ID    |    Refuser : python -m ai_studio refuser ID -n \"raison\"")


def cmd_decide(cfg, db, a, approve):
    from .approvals import decide
    _out(decide(cfg, db, a.id, approve, a.note or ""))


def cmd_taches(cfg, db, a):
    sql, params = "SELECT * FROM tasks", ()
    if a.statut:
        sql += " WHERE status=?"; params = (a.statut,)
    for t in db.q(sql + " ORDER BY id DESC LIMIT 40", params):
        _out(f"#{t['id']} [{t['status']}] {t['agent']:<9} P{t['priority']} {t['title']}  ({t['cost_usd']:.4f} $)")


def cmd_voir(cfg, db, a):
    t = db.task(a.id)
    if not t:
        _out("Tâche introuvable."); return
    _out(f"#{t['id']} [{t['status']}] {t['agent']} — {t['title']}\nCréée par {t['created_by']} | coût {t['cost_usd']:.4f} $\n")
    _out(t["description"]); _out("\n--- Résultat ---"); _out(t["result"] or "(aucun)")


def cmd_journal(cfg, db, a):
    for e in reversed(db.q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (a.n,))):
        _out(f"{e['ts'][5:16].replace('T', ' ')} {e['agent']:<12} {e['kind']:<10} {e['message']}")


def cmd_couts(cfg, db, a):
    _out("Par jour (7 derniers) :")
    for r in db.q("SELECT day, ROUND(SUM(usd),4) u, SUM(web_searches) s FROM costs GROUP BY day ORDER BY day DESC LIMIT 7"):
        _out(f"  {r['day']} : {r['u']} $  ({r['s']} recherches)")
    _out("Par agent (mois) :")
    for r in db.q("SELECT agent, ROUND(SUM(usd),4) u, SUM(input_tokens) i, SUM(output_tokens) o FROM costs WHERE month=strftime('%Y-%m','now') GROUP BY agent ORDER BY u DESC"):
        _out(f"  {r['agent']:<10} {r['u']} $  (entrée {r['i']} / sortie {r['o']} tokens)")


def cmd_agents(cfg, db, a):
    from .agents import ROLES
    for r in ROLES.values():
        model = cfg["models"]["strong"] if r.model == "strong" else cfg["models"]["default"]
        _out(f"{r.name:<10} {r.pole:<15} {model:<20} outils : {', '.join(r.all_tools())}{' + web_search' if r.web_search else ''}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="ai_studio", description="AI Enterprise Studio — moteur multi-agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    s = sub.add_parser("objectif"); s.add_argument("texte")
    s = sub.add_parser("tache"); s.add_argument("titre"); s.add_argument("-a", "--agent", required=True)
    s.add_argument("-d", "--description"); s.add_argument("-p", "--priorite", type=int, default=3)
    s = sub.add_parser("run"); s.add_argument("--once", action="store_true")
    sub.add_parser("status"); sub.add_parser("validations")
    for name in ("valider", "refuser"):
        s = sub.add_parser(name); s.add_argument("id", type=int); s.add_argument("-n", "--note")
    s = sub.add_parser("taches"); s.add_argument("-s", "--statut")
    s = sub.add_parser("voir"); s.add_argument("id", type=int)
    s = sub.add_parser("journal"); s.add_argument("-n", type=int, default=30)
    sub.add_parser("couts"); sub.add_parser("agents")
    a = p.parse_args(argv)

    cfg = load_config()
    db = DB(cfg.db_path)
    handlers = {"init": cmd_init, "objectif": cmd_objectif, "tache": cmd_tache, "run": cmd_run, "status": cmd_status,
                "validations": cmd_validations, "taches": cmd_taches, "voir": cmd_voir, "journal": cmd_journal,
                "couts": cmd_couts, "agents": cmd_agents,
                "valider": lambda c, d, x: cmd_decide(c, d, x, True), "refuser": lambda c, d, x: cmd_decide(c, d, x, False)}
    try:
        handlers[a.cmd](cfg, db, a)
    except Exception as e:
        _out(f"Erreur : {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
