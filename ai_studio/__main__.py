"""Ligne de commande : python -m ai_studio <commande>

Commandes principales :
  init                         crée config.json et .env à partir des modèles
  objectif "texte" [-a ACT]    nouvel objectif, rattaché à une activité (services, video, ecommerce)
  activites                    coûts d'API, revenus et tâches par activité
  tache "titre" -a agent       confie directement une tâche à un agent (option -d description, -p priorité)
  run                          démarre le moteur en continu (24 h/24)
  run --once                   exécute un seul cycle puis s'arrête (pour tester)
  run --tableau                moteur en continu + tableau de bord (http://127.0.0.1:8765)
  serveur                      tableau de bord seul (sans faire travailler les agents)
  status                       vue d'ensemble : objectifs, tâches, budget, validations en attente
  validations                  liste les actions qui attendent ta validation
  valider ID [-n "note"]       valide une action (l'email part, le lien de paiement est créé…)
  refuser ID [-n "raison"]     refuse une action
  taches [-s statut]           liste les tâches
  voir ID                      affiche le résultat complet d'une tâche
  journal [-n 30]              derniers événements
  couts                        dépenses API par jour, par agent et par modèle
  agents                       liste l'équipe et ses outils
  objectifs                    liste les objectifs
  archiver ID                  met un objectif en pause (et annule ses tâches en attente)
  relancer [ID]                remet en attente les tâches en échec (toutes, ou une seule)
  offres                       catalogue des offres (prix, livrable, délai, lien de paiement)
  commandes [-t]               commandes payées et état de livraison (-t : toutes, livrées comprises)
  consigne N "texte"           donne une consigne sur la commande N (elle fait foi) et relance aussitôt le PDG
  livree N [-n "note"]         marque la commande N comme livrée (livraison faite hors des agents)
  memoire [RECHERCHE]          affiche la mémoire des agents (ou les entrées qui contiennent ces mots)
  oublier ID [ID...]           efface des entrées de mémoire (une information fausse, par exemple)
  retenir SUJET "texte"        ajoute une consigne du propriétaire, prioritaire sur ce que les agents ont mémorisé
  notifier-test                affiche une notification Windows de test
  sauvegarder                  sauvegarde immédiate (la sauvegarde quotidienne est automatique)
  sauvegardes                  liste les sauvegardes
  restaurer NOM                restaure une sauvegarde (moteur arrêté)
  demarrage-auto [--retirer]   lance le moteur automatiquement à l'ouverture de session Windows
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

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
    from .activities import exists
    if a.activite and not exists(db, a.activite):
        _out("Activité inconnue. Liste : python -m ai_studio activites"); return
    oid = db.add_objective(a.texte, a.activite)
    db.log("proprietaire", "objective", f"Nouvel objectif #{oid} : {a.texte}")
    _out(f"Objectif #{oid} enregistré. Le PDG le prendra en charge au prochain cycle.")


def cmd_tache(cfg, db, a):
    from .agents import ROLES
    if a.agent not in ROLES:
        _out(f"Agent inconnu. Choix : {', '.join(ROLES)}"); return
    tid = db.add_task(a.titre, a.agent, a.description or "", a.priorite)
    _out(f"Tâche #{tid} confiée à {a.agent}.")


def _studio(cfg, db):
    from .llm import make_llm
    from .orchestrator import Studio
    return Studio(cfg, db, make_llm(cfg))


def _start_dashboard(cfg, port: int, open_browser: bool):
    from .server import serve_in_thread
    try:
        serve_in_thread(cfg, port)
    except OSError as e:
        _out(f"Tableau de bord non démarré (port {port} occupé ?) : {e}")
        return
    url = f"http://127.0.0.1:{port}"
    from .db import DB
    DB(cfg.db_path).kv_set("dashboard_url", url)   # utilisé par les notifications pour ouvrir la bonne page
    _out(f"Tableau de bord : {url}")
    if open_browser:
        import webbrowser
        webbrowser.open(url)


def cmd_serveur(cfg, db, a):
    import time
    _start_dashboard(cfg, a.port, not a.sans_navigateur)
    _out("Le serveur tourne (Ctrl+C pour arrêter). Le moteur des agents n'est pas lancé par cette commande.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        _out("Serveur arrêté.")


def cmd_run(cfg, db, a):
    st = _studio(cfg, db)
    if getattr(a, "tableau", False) and not a.once:
        _start_dashboard(cfg, a.port, not a.sans_navigateur)
    if a.once:
        from .db import iso
        st.recover()
        started = iso()
        s = st.cycle()
        _out(f"Cycle terminé : {s['ran']} tâche(s) exécutée(s), {s['reviews']} revue(s) planifiée(s)."
             + (f" Pause : {s['paused']}" if s["paused"] else ""))
        if s["error"]:
            _out(f"\nERREUR (configuration à corriger, les tâches restent en attente) :\n{s['error']}")
        failed = db.q("SELECT id, title, result FROM tasks WHERE status='failed' AND updated_at >= ? ORDER BY id DESC LIMIT 5", (started,))
        for t in failed:
            _out(f"Tâche en échec #{t['id']} {t['title']} : {(t['result'] or '')[:200]}")
        if failed:
            _out("→ python -m ai_studio relancer   pour remettre les tâches en échec en attente")
        _out(f"Dépense du jour : {db.spent('day'):.4f} $")
    else:
        st.run_forever(_out)


def cmd_status(cfg, db, a):
    from .llm import models_for, resolve_provider
    b = cfg["budget"]
    _out(f"== {cfg['company']['name']} == (fournisseur : {resolve_provider(cfg)}, modèles : {', '.join(models_for(cfg).values())})")
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
    from .approvals import NotReady, decide
    try:
        _out(decide(cfg, db, a.id, approve, a.note or ""))
    except NotReady as e:
        _out(str(e))


def cmd_relancer(cfg, db, a):
    if a.id:
        n = db.conn.execute("UPDATE tasks SET status='pending', attempts=0 WHERE id=? AND status IN ('failed','blocked')", (a.id,)).rowcount
    else:
        n = db.conn.execute("UPDATE tasks SET status='pending', attempts=0 WHERE status IN ('failed','blocked')").rowcount
    db.conn.commit()
    _out(f"{n} tâche(s) remise(s) en attente.")


def cmd_activites(cfg, db, a):
    from .activities import report
    _out(f"{'Activité':<22}{'État':<13}{'API mois':>10}{'API total':>11}{'Revenus mois':>14}{'Tâches':>8}")
    for r in report(db):
        _out(f"{(r['name'] + (' (' + r['id'] + ')' if r['id'] else ''))[:21]:<22}{r['status']:<13}{r['cost_month']:>9.4f}${r['cost_total']:>10.4f}$"
             f"{r['revenue_month']:>13.2f}€{r['tasks_done']:>5}/{r['tasks_open']}")
    _out("\nTâches : terminées/en cours. Un objectif se rattache à une activité : objectif \"texte\" -a video")


def cmd_objectifs(cfg, db, a):
    rows = db.q("SELECT * FROM objectives ORDER BY id")
    if not rows:
        _out("Aucun objectif."); return
    for o in rows:
        _out(f"#{o['id']} [{o['status']}]{' [' + o['activity'] + ']' if o['activity'] else ''} {o['text']}")
    _out("\nMettre en pause : python -m ai_studio archiver ID   (les tâches de revue en attente sont annulées)")


def cmd_archiver(cfg, db, a):
    o = db.one("SELECT * FROM objectives WHERE id=?", (a.id,))
    if not o:
        _out("Objectif introuvable."); return
    db.run("UPDATE objectives SET status='paused' WHERE id=?", (a.id,))
    n = db.conn.execute("UPDATE tasks SET status='blocked', result='Objectif mis en pause par le propriétaire' "
                        "WHERE objective_id=? AND status='pending'", (a.id,)).rowcount
    db.conn.commit()
    db.log("proprietaire", "objective", f"Objectif #{a.id} mis en pause ({n} tâche(s) en attente annulée(s))")
    _out(f"Objectif #{a.id} mis en pause, {n} tâche(s) en attente annulée(s).")


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
    import os
    from .agents import ROLES
    from .llm import models_for, resolve_provider
    from .tools import REGISTRY
    m = models_for(cfg)
    search = "Brave" if os.environ.get("BRAVE_API_KEY") else ("Anthropic (serveur)" if resolve_provider(cfg) == "anthropic" else "aucune (ajoute BRAVE_API_KEY)")
    _out(f"Fournisseur : {resolve_provider(cfg)} — recherche web : {search}\n")
    for r in ROLES.values():
        model = m["strong"] if r.model == "strong" else m["default"]
        _out(f"{r.name:<10} {r.pole:<15} {model:<20} outils : {', '.join(n for n in r.all_tools() if n in REGISTRY and REGISTRY[n].available())}{' + recherche web' if r.web_search else ''}")


def cmd_offres(cfg, db, a):
    from .commerce import offer_sheet
    rows = db.q("SELECT * FROM offers WHERE status='active' ORDER BY id")
    if not rows:
        _out("Catalogue vide : les agents créent les fiches avec save_offer (demande-le au PDG par un objectif).")
    for o in rows:
        _out(f"#{o['id']} {offer_sheet(o)}\n- Lien de paiement : {o['payment_link_url'] or 'aucun'}\n")


def cmd_importer_offres(cfg, db, a):
    from .commerce import import_offers
    path = Path(a.fichier) if a.fichier else Path(__file__).resolve().parents[1] / "catalogue" / "offres.json"
    try:
        names = import_offers(db, path)
    except (OSError, ValueError, KeyError) as e:
        _out(f"Import impossible : {e}"); return
    _out(f"{len(names)} offre(s) à jour : " + ", ".join(names) + ". Voir le détail avec : python -m ai_studio offres")


def cmd_commande_test(cfg, db, a):
    from .commerce import test_order
    try:
        n = test_order(cfg, db, a.offre, a.email, a.infos or "")
    except ValueError as e:
        _out(str(e)); return
    _out(f"Commande d'essai n°{n} créée (sans paiement). Le PDG organise la production ; la livraison partira vers "
         f"{a.email} après ta validation. Suivi : python -m ai_studio commandes")


def cmd_client(cfg, db, a):
    from .approvals import EMAIL_RE
    from .commerce import get_order
    o = get_order(db, a.n)
    if not o:
        _out(f"Commande n°{a.n} introuvable."); return
    if not EMAIL_RE.match(a.email.strip()):
        _out("Adresse email invalide."); return
    db.run("UPDATE sales SET customer_email=? WHERE id=?", (a.email.strip(), o["id"]))
    db.log("proprietaire", "commande", f"Commande n°{a.n} : email du client corrigé ({o['customer_email']} → {a.email.strip()})")
    _out(f"Commande n°{a.n} : les prochains envois partiront vers {a.email.strip()}.")


def cmd_commandes(cfg, db, a):
    from .commerce import STAGES
    sql = "SELECT rowid AS n, * FROM sales" + ("" if a.toutes else " WHERE stage IN ('a_livrer','attente_client')")
    rows = db.q(sql + " ORDER BY created_at DESC LIMIT 50")
    if not rows:
        _out("Aucune commande en cours." if not a.toutes else "Aucune commande.")
    for s in rows:
        _out(f"n°{s['n']:<4} {s['created_at'][:16]}  {STAGES.get(s['stage'], s['stage']):<20} {s['amount']:>8.2f} {s['currency']}"
             f"{'' if s['livemode'] else ' (test)'}  {s['product'] or '?'}  {s['customer_email'] or '?'}"
             + (f"  promise avant {s['due_at'][:16]}" if s["due_at"] else "") + (f"  relances : {s['nudges']}" if s["nudges"] else ""))


def cmd_supprimer_mission(cfg, db, a):
    from .reset import delete_mission
    kind = "commande" if a.commande else "objectif"
    if not a.confirmer:
        what = "commande d'essai n°" if a.commande else "mission #"
        _out(f"Supprime la {what}{a.id}, ses tâches, validations et "
             "son journal (sauvegarde faite avant ; coûts, bibliothèque, mémoire et fichiers conservés). "
             "Ajoute --confirmer pour le faire.")
        return
    r = delete_mission(cfg, db, kind, a.id)
    _out(f"{r['title']} supprimée : {r['tasks']} tâche(s), {r['approvals']} validation(s).\nSauvegarde : {r['backup']}")


def cmd_bilan(cfg, db, a):
    from .reports import monthly, to_markdown
    _out(to_markdown(monthly(cfg, db, a.mois or None)))


def cmd_mode_test(cfg, db, a):
    """Active ou coupe le mode test dans config.json (le reste du fichier est conservé)."""
    import json as _json
    from .config import EMAIL_RE as _re_ok  # noqa: F401  (validation ci-dessous)
    path = cfg.root / "config.json"
    data = _json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    tm = data.setdefault("company", {}).setdefault("test_mode", {"enabled": False, "recipients": []})
    if a.etat == "on":
        bad = [e for e in a.emails if not _re_ok.match(e)]
        if bad or not a.emails:
            raise ValueError("Donne au moins une adresse valide : mode-test on ton@adresse.fr" + (f" (invalide : {', '.join(bad)})" if bad else ""))
        tm.update(enabled=True, recipients=a.emails)
    elif a.etat == "off":
        tm["enabled"] = False
    if a.etat in ("on", "off"):
        path.write_text(_json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    on = tm.get("enabled")
    _out(("Mode test ACTIF : identité légale fictive, emails limités à " + ", ".join(tm.get("recipients", []))
          if on else "Mode test désactivé : les emails utilisent la vraie identité (company.legal).")
         + ("\nRedémarre le moteur pour que les agents en tiennent compte." if a.etat in ("on", "off") else ""))


def cmd_consigne(cfg, db, a):
    from .commerce import add_owner_note, delivery_brief, get_order
    s = get_order(db, a.n)
    if not s or s["stage"] in ("livree", "historique"):
        _out("Commande introuvable ou déjà livrée."); return
    add_owner_note(db, a.n, a.texte)
    tid = db.add_task(f"Commande n°{a.n} : nouvelle consigne du propriétaire", "pdg",
                      delivery_brief(db, a.n, "NOUVELLE CONSIGNE DU PROPRIÉTAIRE : applique-la maintenant.\n\n"),
                      priority=1, created_by="proprietaire", order_ref=a.n)
    _out(f"Consigne ajoutée à la commande n°{a.n} ; le PDG s'en occupe (tâche #{tid}).")


def cmd_livree(cfg, db, a):
    from .commerce import get_order, mark_delivered
    if not get_order(db, a.n):
        _out("Commande introuvable."); return
    mark_delivered(db, a.n, a.note or "Marquée livrée par le propriétaire")
    _out(f"Commande n°{a.n} marquée livrée : le moteur ne la relancera plus.")


def cmd_memoire(cfg, db, a):
    if a.recherche:
        words = [w.lower() for w in a.recherche.split() if w]
        cond = " AND ".join("(lower(topic) LIKE ? OR lower(content) LIKE ?)" for _ in words)
        params = [p for w in words for p in (f"%{w}%", f"%{w}%")]
        rows = db.q(f"SELECT * FROM memory WHERE {cond} ORDER BY id DESC LIMIT ?", (*params, a.n))
    else:
        rows = db.q("SELECT * FROM memory ORDER BY id DESC LIMIT ?", (a.n,))
    if not rows:
        _out("Aucune entrée.")
    for m in rows:
        text = " ".join(m["content"].split())
        _out(f"#{m['id']:<4} {m['created_at'][:10]} {m['agent']:<12} [{m['topic']}]\n      {text[:220]}{'…' if len(text) > 220 else ''}")
    if rows:
        _out("\nPour effacer : python -m ai_studio oublier ID [ID…]")


def cmd_oublier(cfg, db, a):
    for i in a.ids:
        m = db.one("SELECT topic FROM memory WHERE id=?", (i,))
        if not m:
            _out(f"#{i} : introuvable"); continue
        db.run("DELETE FROM memory WHERE id=?", (i,))
        db.log("proprietaire", "memoire", f"Entrée de mémoire #{i} effacée ({m['topic']})")
        _out(f"#{i} effacée ({m['topic']})")


def cmd_retenir(cfg, db, a):
    mid = db.remember("proprietaire", a.sujet, a.texte)
    db.log("proprietaire", "memoire", f"Consigne mémorisée #{mid} ({a.sujet})")
    _out(f"Consigne #{mid} mémorisée : les agents la verront en premier quand ils chercheront « {a.sujet} ».")


def cmd_notifier_test(cfg, db, a):
    from . import notify
    if notify.runner is None:
        _out("Les notifications Windows ne fonctionnent que sous Windows.")
        return
    ok = notify.send(cfg, db, "AI Enterprise Studio", "Notification de test : tu seras prévenu ici quand une validation t'attend.")
    if ok:
        _out("Windows a accepté la notification. Si elle n'apparaît pas à l'écran, regarde le centre de notifications "
             "(Windows + N) : le mode « Ne pas déranger » ou les réglages de « Windows PowerShell » dans "
             "Paramètres > Système > Notifications peuvent la masquer.")
    else:
        last = db.one("SELECT message FROM events WHERE kind='notification' ORDER BY id DESC LIMIT 1")
        _out("La notification n'a pas pu être affichée.\n" + (last[0] if last else ""))


def cmd_repartir_propre(cfg, db, a):
    if not a.confirmer:
        _out("Remise à zéro : supprime missions, tâches, journal, validations, discussions et commandes d'essai ; déplace "
             "les fichiers de travail dans data/archives/. Conserve configuration, offres, ventes réelles, coûts, emails, "
             "bibliothèque de recherches et ta mémoire. Une sauvegarde complète est faite avant.\n"
             "Arrête d'abord le moteur, puis relance avec --confirmer (ajoute --memoire-agents pour effacer aussi les "
             "souvenirs des agents).")
        return
    from .reset import fresh_start
    r = fresh_start(cfg, db, a.memoire_agents)
    _out(f"Remise à zéro faite. Sauvegarde : {r['backup']}\n"
         f"Supprimé : {r['tasks']} tâche(s), {r['objectives']} mission(s), {r['approvals']} validation(s), "
         f"{r['test_orders']} commande(s) d'essai" + (f", {r['agent_memories']} souvenir(s) d'agents" if a.memoire_agents else "") +
         (f".\nFichiers de travail déplacés dans : {r['workspace_archive']}" if r.get("workspace_archive") else ".") +
         "\nRelance le moteur (demarrer.bat).")


def cmd_sauvegarder(cfg, db, a):
    from .backup import make_backup
    _out(f"Sauvegarde créée : {make_backup(cfg, db, datetime.now().strftime('%Y-%m-%d_%H%M%S'))}")


def cmd_sauvegardes(cfg, db, a):
    from .backup import list_backups
    lst = list_backups(cfg)
    if not lst:
        _out("Aucune sauvegarde pour l'instant (la première est faite au premier cycle du moteur).")
    for b in lst:
        _out(f"{b['name']:<20} {b['size'] / 1024:>8.0f} Ko   {b['path']}")


def cmd_restaurer(cfg, db, a):
    from .backup import restore
    db.conn.close()
    _out(f"Base restaurée depuis la sauvegarde {a.nom} ({restore(cfg, a.nom)}). L'ancienne base est conservée à côté.")


def startup_file() -> Path:
    import os
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AI Enterprise Studio.cmd"


def cmd_demarrage_auto(cfg, db, a):
    if sys.platform != "win32":
        _out("Le démarrage automatique ne concerne que Windows.")
        return
    f = startup_file()
    if a.retirer:
        f.unlink(missing_ok=True)
        _out("Démarrage automatique désactivé.")
        return
    bat = ROOT / "demarrer.bat"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f'@echo off\r\nstart "AI Enterprise Studio" /min cmd /c ""{bat}" --auto"\r\n', encoding="utf-8")
    _out(f"Démarrage automatique activé : le moteur se lancera (fenêtre réduite) à chaque ouverture de session.\n"
         f"Fichier créé : {f}\nPour l'enlever : python -m ai_studio demarrage-auto --retirer")


def main(argv=None):
    p = argparse.ArgumentParser(prog="ai_studio", description="AI Enterprise Studio — moteur multi-agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    s = sub.add_parser("objectif"); s.add_argument("texte")
    s.add_argument("-a", "--activite", help="services, video, ecommerce… (vide = transverse)")
    sub.add_parser("activites")
    s = sub.add_parser("tache"); s.add_argument("titre"); s.add_argument("-a", "--agent", required=True)
    s.add_argument("-d", "--description"); s.add_argument("-p", "--priorite", type=int, default=3)
    s = sub.add_parser("run"); s.add_argument("--once", action="store_true")
    s.add_argument("--tableau", action="store_true", help="démarre aussi le tableau de bord")
    s.add_argument("--port", type=int, default=8765); s.add_argument("--sans-navigateur", action="store_true")
    s = sub.add_parser("serveur"); s.add_argument("--port", type=int, default=8765); s.add_argument("--sans-navigateur", action="store_true")
    sub.add_parser("status"); sub.add_parser("validations")
    for name in ("valider", "refuser"):
        s = sub.add_parser(name); s.add_argument("id", type=int); s.add_argument("-n", "--note")
    s = sub.add_parser("taches"); s.add_argument("-s", "--statut")
    s = sub.add_parser("voir"); s.add_argument("id", type=int)
    s = sub.add_parser("journal"); s.add_argument("-n", type=int, default=30)
    sub.add_parser("couts"); sub.add_parser("agents"); sub.add_parser("objectifs")
    s = sub.add_parser("relancer"); s.add_argument("id", type=int, nargs="?")
    s = sub.add_parser("archiver"); s.add_argument("id", type=int)
    sub.add_parser("offres")
    s = sub.add_parser("repartir-propre"); s.add_argument("--confirmer", action="store_true"); s.add_argument("--memoire-agents", action="store_true")
    s = sub.add_parser("client"); s.add_argument("n", type=int); s.add_argument("email")
    s = sub.add_parser("importer-offres"); s.add_argument("fichier", nargs="?")
    s = sub.add_parser("commande-test"); s.add_argument("offre"); s.add_argument("-e", "--email", required=True); s.add_argument("-i", "--infos")
    s = sub.add_parser("commandes"); s.add_argument("-t", "--toutes", action="store_true")
    s = sub.add_parser("supprimer-mission"); s.add_argument("id", type=int); s.add_argument("--commande", action="store_true"); s.add_argument("--confirmer", action="store_true")
    s = sub.add_parser("bilan"); s.add_argument("mois", nargs="?", help="AAAA-MM (mois en cours par défaut)")
    s = sub.add_parser("mode-test"); s.add_argument("etat", nargs="?", choices=["on", "off"]); s.add_argument("emails", nargs="*")
    s = sub.add_parser("consigne"); s.add_argument("n", type=int); s.add_argument("texte")
    s = sub.add_parser("livree"); s.add_argument("n", type=int); s.add_argument("-n", "--note")
    s = sub.add_parser("memoire"); s.add_argument("recherche", nargs="?", default=""); s.add_argument("-n", type=int, default=30)
    s = sub.add_parser("oublier"); s.add_argument("ids", type=int, nargs="+")
    s = sub.add_parser("retenir"); s.add_argument("sujet"); s.add_argument("texte")
    sub.add_parser("notifier-test"); sub.add_parser("sauvegarder"); sub.add_parser("sauvegardes")
    s = sub.add_parser("restaurer"); s.add_argument("nom")
    s = sub.add_parser("demarrage-auto"); s.add_argument("--retirer", action="store_true")
    a = p.parse_args(argv)

    cfg = load_config()
    db = DB(cfg.db_path)
    handlers = {"init": cmd_init, "objectif": cmd_objectif, "tache": cmd_tache, "run": cmd_run, "status": cmd_status,
                "validations": cmd_validations, "taches": cmd_taches, "voir": cmd_voir, "journal": cmd_journal,
                "couts": cmd_couts, "agents": cmd_agents, "relancer": cmd_relancer, "objectifs": cmd_objectifs, "activites": cmd_activites,
                "archiver": cmd_archiver, "serveur": cmd_serveur, "notifier-test": cmd_notifier_test,
                "offres": cmd_offres, "client": cmd_client, "repartir-propre": cmd_repartir_propre, "importer-offres": cmd_importer_offres, "commande-test": cmd_commande_test, "commandes": cmd_commandes, "livree": cmd_livree, "consigne": cmd_consigne, "mode-test": cmd_mode_test, "bilan": cmd_bilan, "supprimer-mission": cmd_supprimer_mission,
                "memoire": cmd_memoire, "oublier": cmd_oublier, "retenir": cmd_retenir,
                "sauvegarder": cmd_sauvegarder, "sauvegardes": cmd_sauvegardes, "restaurer": cmd_restaurer,
                "demarrage-auto": cmd_demarrage_auto,
                "valider": lambda c, d, x: cmd_decide(c, d, x, True), "refuser": lambda c, d, x: cmd_decide(c, d, x, False)}
    try:
        handlers[a.cmd](cfg, db, a)
    except Exception as e:
        _out(f"Erreur : {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
