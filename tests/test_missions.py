"""Tableau des missions : avancement, anomalies, vérification du propriétaire, libellés du fil d'activité."""
import unittest
from datetime import timedelta

from ai_studio import missions
from ai_studio.db import iso, now
from test_core import Base


class MissionsTest(Base):
    def test_board_detects_problems_and_owner_reopens(self):
        oid = self.db.add_objective("Trouver 20 artisans lyonnais sans site web", "services")
        root = self.db.add_task("Revue de l'objectif #1", "pdg", objective_id=oid)
        child = self.db.add_task("Lister les artisans", "recherche", parent_id=root)       # hérite de la mission
        self.assertEqual(self.db.task(child)["objective_id"], oid)
        self.db.set_task(root, status="done", result="délégué")
        self.db.set_task(child, status="failed", result="Erreur API")
        self.db.run("UPDATE objectives SET status='done' WHERE id=?", (oid,))              # le PDG la déclare terminée
        m = next(x for x in missions.missions(self.db) if x["kind"] == "objectif")
        self.assertEqual((m["declared_done"], m["total"], m["progress"]), (True, 2, 50))
        self.assertIn("1 tâche(s) en échec", m["issues"])
        self.assertEqual(missions.labels_for_tasks(self.db, [child])[child]["label"], "Mission #1 · Trouver 20 artisans lyonnais sans site web")
        with self.assertRaises(ValueError):
            missions.verify(self.cfg, self.db, "objectif", oid, "issue", "")                   # il faut dire ce qui ne va pas
        out = missions.verify(self.cfg, self.db, "objectif", oid, "issue", "La liste n'a jamais été produite")
        self.assertIn("renvoyée au PDG", out)
        o = self.db.one("SELECT * FROM objectives WHERE id=?", (oid,))
        self.assertEqual((o["status"], o["owner_check"]), ("active", "issue"))
        t = self.db.one("SELECT * FROM tasks ORDER BY id DESC LIMIT 1")
        self.assertEqual((t["agent"], t["priority"], t["objective_id"]), ("pdg", 1, oid))
        self.assertIn("jamais été produite", t["description"]); self.assertIn("[failed]", t["description"])
        # le PDG la redéclare terminée : la vérification précédente ne compte plus
        from ai_studio.tools import ToolContext, run_tool
        run_tool("set_objective_status", ToolContext(self.cfg, self.db, "pdg", None, []), {"objective_id": oid, "status": "done", "note": "liste livrée"})
        self.assertIsNone(self.db.one("SELECT owner_check FROM objectives WHERE id=?", (oid,))[0])
        self.assertIn("terminée", missions.verify(self.cfg, self.db, "objectif", oid, "ok"))
        self.assertEqual(self.db.one("SELECT owner_check, status FROM objectives WHERE id=?", (oid,))[:], ("ok", "done"))

    def test_unclosed_waiting_and_archive(self):
        oid = self.db.add_objective("Documents légaux")
        tid = self.db.add_task("Rédiger les CGV", "juridique", objective_id=oid)
        self.db.set_task(tid, status="done", result="CGV rédigées")
        m = missions.missions(self.db)[0]
        self.assertTrue(m["to_check"])                                      # tout est fini mais personne n'a clôturé
        self.assertIn("plus personne n'y travaille", m["issues"][0])
        aid = self.db.add_approval("juridique", tid, "decision", {"question": "Je publie ?"}, "r")
        m = missions.missions(self.db)[0]
        self.assertEqual((m["waiting"], m["to_check"], m["issues"]), ([aid], False, []))   # elle attend le propriétaire
        dup = self.db.add_objective("Doublon")
        pending = self.db.add_task("t", "recherche", objective_id=dup)
        self.assertIn("archivée", missions.verify(self.cfg, self.db, "objectif", dup, "archive", "doublon"))
        self.assertEqual(self.db.task(pending)["status"], "blocked")
        m = next(x for x in missions.missions(self.db) if x["id"] == dup and x["kind"] == "objectif")
        self.assertEqual((m["archived"], m["issues"], m["to_check"]), (True, [], False))

    def test_simple_mission_not_reviewed_by_ceo(self):
        from ai_studio.tools import ToolContext, run_tool
        from ai_studio.agents import ROLES
        from ai_studio.orchestrator import Studio
        out = run_tool("launch_mission", ToolContext(self.cfg, self.db, "pdg", None, list(ROLES)),
                       {"title": "Proposer un audit SEO à ax-07.fr", "instructions": "Contacter le propriétaire du site", "agent": "ventes"})
        oid = int(out.split("#")[1].split()[0])
        t = self.db.one("SELECT * FROM tasks WHERE objective_id=?", (oid,))
        self.assertIn("DÉJÀ été traitée", t["description"])
        self.db.set_task(t["id"], status="done", result="Email de prospection préparé")
        Studio(self.cfg, self.db, None).schedule_reviews()
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM tasks WHERE agent='pdg'")[0], 0)        # pas de revue en double
        self.assertTrue(next(m for m in missions.missions(self.db) if m["id"] == oid)["to_check"])  # directement au propriétaire
        missions.verify(self.cfg, self.db, "objectif", oid, "issue", "Aucun email n'a été envoyé")
        self.assertIsNone(self.db.one("SELECT simple FROM objectives WHERE id=?", (oid,))[0])         # le PDG reprend la main

    def test_costs_per_mission_and_task(self):
        from pathlib import Path as P
        from ai_studio import commerce
        from ai_studio.server import costs
        commerce.import_offers(self.db, P(__file__).resolve().parents[1] / "catalogue" / "offres.json")
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "proprio@studio.fr")
        oid = self.db.add_objective("Prospection")
        a = self.db.add_task("Lister", "recherche", objective_id=oid)
        b = self.db.add_task("Rapport", "atelier", order_ref=n)
        self.db.add_cost("recherche", a, "m", 1, 1, 0, 0.10); self.db.add_cost("recherche", a, "m", 1, 1, 0, 0.05)
        self.db.add_cost("atelier", b, "m", 1, 1, 0, 0.50)
        self.db.add_cost("pdg", None, "m", 1, 1, 0, 0.02)                       # discussion : hors mission
        ms = {(m["kind"], m["id"]): m for m in missions.missions(self.db)}
        self.assertEqual((ms[("objectif", oid)]["cost_usd"], ms[("commande", n)]["cost_usd"]), (0.15, 0.5))
        self.assertEqual(missions.detail(self.db, "objectif", oid)["tasks"][0]["cost_usd"], 0.15)
        c = costs(self.cfg, self.db)
        order = next(r for r in c["by_mission"] if r["kind"] == "commande")
        self.assertEqual((order["revenue_eur"], order["margin_eur"]), (89, round(89 - (89 * 0.028 + 0.25) - 89 * 0.256 - 0.5 * 0.92, 2)))   # Stripe, cotisations, API
        self.assertEqual(c["outside_missions_month_usd"], 0.02)

    def test_stuck_and_idle_signals(self):
        oid = self.db.add_objective("Mission test")
        tid = self.db.add_task("t", "recherche", objective_id=oid)
        self.db.run("UPDATE tasks SET status='running', updated_at=? WHERE id=?", (iso(now() - timedelta(hours=2)), tid))
        m = missions.missions(self.db)[0]
        self.assertTrue(any("en cours depuis plus de" in i for i in m["issues"]))

    def test_order_mission_and_feed_label(self):
        from pathlib import Path
        from ai_studio import commerce
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")
        n = commerce.test_order(self.cfg, self.db, "Site vitrine", "proprio@studio.fr")
        m = next(x for x in missions.missions(self.db) if x["kind"] == "commande")
        self.assertEqual((m["title"], m["test"], m["state"]), (f"Commande n°{n} · Site vitrine", True, "Attente client"))   # infos à demander
        missions.verify(self.cfg, self.db, "commande", n, "issue", "Le site n'a pas de page contact")
        self.assertIn("page contact", commerce.get_order(self.db, n)["owner_notes"])
        from ai_studio.server import events
        ev = events(self.cfg, self.db)
        self.assertTrue(any(e["mission"] and e["mission"]["label"].startswith(f"Commande n°{n}") for e in ev))


class DeleteMissionTest(Base):
    def test_delete_objective_mission(self):
        keep = self.db.add_objective("Autre mission")
        kt = self.db.add_task("Garder", "ventes", objective_id=keep)
        oid = self.db.add_objective("Prospection ax-07")
        kick = missions.assign_pm(self.db, "objectif", oid)
        step = self.db.add_task("Email", "ventes", parent_id=kick)
        self.db.add_approval("ventes", step, "send_email", {"to": "a@b.fr", "subject": "s", "body": "b"}, "r")
        self.db.log("ventes", "info", "trace", step)
        self.db.run("INSERT INTO costs(ts, day, month, agent, task_id, model, usd) VALUES (?,?,?,?,?,?,?)", (iso(), "2026-11-28", "2026-11", "ventes", step, "m", 0.01))
        self.db.set_task(step, status="running")
        with self.assertRaises(ValueError):                                  # un agent y travaille : refus
            missions.verify(self.cfg, self.db, "objectif", oid, "delete")
        self.db.set_task(step, status="done")
        msg = missions.verify(self.cfg, self.db, "objectif", oid, "delete")
        self.assertIn("supprimée : 2 tâche(s), 1 validation(s)", msg)
        self.assertIsNone(self.db.one("SELECT 1 FROM objectives WHERE id=?", (oid,)))
        self.assertIsNone(self.db.one("SELECT 1 FROM tasks WHERE objective_id=?", (oid,)))
        self.assertFalse(self.db.pending_approvals())
        self.assertIsNone(self.db.one("SELECT 1 FROM events WHERE task_id=?", (step,)))
        self.assertTrue(self.db.one("SELECT 1 FROM costs WHERE task_id=?", (step,)))      # budget exact
        self.assertTrue(self.db.task(kt))                                                   # l'autre mission reste
        self.assertTrue(any((self.cfg.data_dir / "sauvegardes").glob("*avant-suppression-objectif*"))
                        or any(self.cfg.data_dir.rglob("*avant-suppression-objectif*")))
        with self.assertRaises(KeyError):
            missions.verify(self.cfg, self.db, "objectif", oid, "delete")

    def test_real_order_cannot_be_deleted(self):
        from pathlib import Path
        from ai_studio import commerce
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")
        n = commerce.test_order(self.cfg, self.db, "Site vitrine", "proprio@studio.fr")
        self.assertIn("supprimée", missions.verify(self.cfg, self.db, "commande", n, "delete"))
        self.assertIsNone(commerce.get_order(self.db, n))
        n2 = commerce.test_order(self.cfg, self.db, "Site vitrine", "proprio@studio.fr")
        self.db.run("UPDATE sales SET livemode=1 WHERE rowid=?", (n2,))
        with self.assertRaises(ValueError):
            missions.verify(self.cfg, self.db, "commande", n2, "delete")


class ProjectManagerTest(Base):
    def test_only_project_manager_distributes_work(self):
        from ai_studio.tools import run_tool
        oid = self.db.add_objective("Prospection")
        kick = missions.assign_pm(self.db, "objectif", oid)
        step = self.db.add_task("Vérifier le contact", "ventes", parent_id=kick)
        out = run_tool("delegate_task", self.ctx("ventes", step), {"agent": "chef_projet", "title": "Résultat", "description": "x"})
        self.assertIn("pilotée par un chef de projet", out)
        out = run_tool("delegate_task", self.ctx("chef_projet", kick), {"agent": "ventes", "title": "Rédiger l'email", "description": "x"})
        self.assertIn("créée", out)
        free = self.db.add_task("Hors mission", "ventes")
        self.assertIn("créée", run_tool("delegate_task", self.ctx("ventes", free), {"agent": "recherche", "title": "t", "description": "d"}))

    def test_stalled_mission_relaunched_then_escalated(self):
        oid = self.db.add_objective("Prospection")
        kick = missions.assign_pm(self.db, "objectif", oid)
        self.db.set_task(kick, status="done", result="plan")
        r1 = missions.relaunch_if_stalled(self.db, self.db.task(kick))
        self.assertTrue(self.db.task(r1)["title"].startswith("Relance"))
        self.assertIsNone(missions.relaunch_if_stalled(self.db, self.db.task(kick)))         # déjà relancé : on attend
        self.db.set_task(r1, status="done", result="rien")
        r2 = missions.relaunch_if_stalled(self.db, self.db.task(r1))
        self.db.set_task(r2, status="done", result="rien")
        esc = missions.relaunch_if_stalled(self.db, self.db.task(r2))
        self.assertEqual((self.db.task(esc)["agent"], self.db.task(esc)["priority"]), ("pdg", 1))   # arbitrage du PDG
        self.db.set_task(esc, status="done", result="rien")
        self.assertIsNone(missions.relaunch_mission(self.db, "objectif", oid))                    # pas de boucle infinie
        self.assertIn("ta décision est nécessaire", self.db.one("SELECT message FROM events ORDER BY id DESC LIMIT 1")[0])
        # le propriétaire (via le PDG) peut toujours relancer avec une consigne
        out = __import__("ai_studio.tools", fromlist=["run_tool"]).run_tool(
            "assign_project_manager", self.ctx("pdg"), {"kind": "objectif", "id": oid, "brief": "Rédige et soumets l'email maintenant"})
        self.assertIn("relancé", out)
        self.assertIn("Rédige et soumets", self.db.one("SELECT description FROM tasks ORDER BY id DESC LIMIT 1")[0])

    def test_interleaved_relaunches_still_escalate(self):
        # cas réel : à chaque relance le chef de projet délègue une étape qui ne débloque rien
        oid = self.db.add_objective("Prospection")
        kick = missions.assign_pm(self.db, "objectif", oid)
        self.db.set_task(kick, status="done", result="plan")
        titles = []
        for _ in range(2):
            r = missions.relaunch_mission(self.db, "objectif", oid)
            titles.append(self.db.task(r)["title"])
            self.db.set_task(r, status="done", result="délégué")
            step = self.db.add_task("Étape", "juridique", parent_id=r)
            self.db.set_task(step, status="done", result="avis conditionnel")
            follow = self.db.add_task("Suivi : étape terminée", "chef_projet", parent_id=step)
            self.db.set_task(follow, status="done", result="toujours bloqué")
        esc = missions.relaunch_mission(self.db, "objectif", oid)
        self.assertTrue(all(t.startswith("Relance") for t in titles))
        self.assertEqual(self.db.task(esc)["agent"], "pdg")
        self.db.set_task(esc, status="done", result="relancé le chef de projet")
        r = missions.relaunch_mission(self.db, "objectif", oid)               # une relance après l'arbitrage
        self.db.set_task(r, status="done", result="rien")
        r = missions.relaunch_mission(self.db, "objectif", oid)
        self.db.set_task(r, status="done", result="rien")
        self.assertIsNone(missions.relaunch_mission(self.db, "objectif", oid))      # stop : décision du propriétaire
        dec = self.db.one("SELECT * FROM approvals WHERE action='decision' AND status='pending'")
        self.assertIn("Mission bloquée", dec["reason"])
        self.assertIsNone(missions.relaunch_mission(self.db, "objectif", oid))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM approvals WHERE action='decision'")[0], 1)
        # la réponse du propriétaire relance la mission, et les compteurs repartent de zéro
        from ai_studio.approvals import decide
        decide(self.cfg, self.db, dec["id"], True, "Le juridique n'est pas bloquant : soumets l'email")
        follow = self.db.one("SELECT * FROM tasks ORDER BY id DESC LIMIT 1")
        self.assertEqual((follow["agent"], follow["objective_id"]), ("chef_projet", oid))
        self.db.set_task(follow["id"], status="done", result="rien")
        self.assertTrue(self.db.task(missions.relaunch_mission(self.db, "objectif", oid))["title"].startswith("Relance"))

    def test_engine_relaunches_stalled_mission_without_waiting(self):
        from ai_studio.orchestrator import Studio
        oid = self.db.add_objective("Prospection")
        kick = missions.assign_pm(self.db, "objectif", oid)
        self.db.set_task(kick, status="done", result="plan")
        self.db.run("UPDATE objectives SET last_review_at=? WHERE id=?", (iso(), oid))          # point périodique récent
        Studio(self.cfg, self.db, None).schedule_reviews()
        self.assertTrue(self.db.one("SELECT 1 FROM tasks WHERE agent='chef_projet' AND title LIKE 'Relance%' AND status='pending'"))

    def test_silent_task_is_not_a_success(self):
        from ai_studio.runner import run_task
        from ai_studio.llm import BudgetGuard
        from test_core import FakeLLM
        oid = self.db.add_objective("Prospection")
        missions.assign_pm(self.db, "objectif", oid)
        self.db.run("UPDATE tasks SET status='done' WHERE agent='chef_projet'")
        t = self.db.add_task("Préparer l'email", "ventes", objective_id=oid)
        llm = FakeLLM([[], []])                                                  # l'agent ne dit rien
        self.cfg.data["runtime"]["max_attempts"] = 2
        g = BudgetGuard(self.cfg, self.db)
        self.assertEqual(run_task(self.cfg, self.db, llm, g, self.db.task(t)), "pending")
        self.assertEqual(run_task(self.cfg, self.db, llm, g, self.db.task(t)), "failed")
        self.assertIn("sans résultat", self.db.task(t)["result"])
        self.assertTrue(self.db.one("SELECT 1 FROM tasks WHERE agent='chef_projet' AND status='pending'"))   # chef de projet prévenu

    def ctx(self, agent, task_id=None):
        from ai_studio.agents import ROLES
        from ai_studio.tools import ToolContext
        return ToolContext(self.cfg, self.db, agent, self.db.task(task_id) if task_id else None, list(ROLES))

    def test_full_cycle(self):
        from ai_studio.tools import run_tool
        oid = self.db.add_objective("Créer 3 articles de blog sur le SEO local")
        out = run_tool("assign_project_manager", self.ctx("pdg"), {"kind": "objectif", "id": oid, "brief": "Priorité : qualité"})
        self.assertIn("Chef de projet nommé", out)
        self.assertIn("déjà un chef de projet", run_tool("assign_project_manager", self.ctx("pdg"), {"kind": "objectif", "id": oid}))
        kick = self.db.one("SELECT * FROM tasks WHERE agent='chef_projet'")
        self.assertEqual((kick["objective_id"], kick["priority"]), (oid, 2))
        self.assertIn("Priorité : qualité", kick["description"])
        self.assertIn("Demande du propriétaire : Créer 3 articles", run_tool("mission_status", self.ctx("chef_projet", kick["id"]), {}))
        # le chef de projet délègue deux étapes ; à la fin de chacune, il est réveillé une seule fois
        self.db.set_task(kick["id"], status="done", result="plan fait")
        a = self.db.add_task("Article 1", "marketing", parent_id=kick["id"]); b = self.db.add_task("Article 2", "marketing", parent_id=kick["id"])
        for t in (a, b):
            self.db.set_task(t, status="done", result="article écrit")
            missions.wake_pm(self.db, self.db.task(t))
        wake = self.db.q("SELECT * FROM tasks WHERE agent='chef_projet' AND status='pending'")
        self.assertEqual(len(wake), 1)
        self.assertIn("Article 1", wake[0]["description"]); self.assertIn("Article 2", wake[0]["description"])
        self.assertIsNone(missions.wake_pm(self.db, self.db.task(wake[0]["id"])))          # pas de réveil sur ses propres tâches
        # clôture : refusée tant qu'il reste du travail, puis acceptée et soumise au propriétaire
        c = self.db.add_task("Article 3", "marketing", objective_id=oid)
        ctx = self.ctx("chef_projet", wake[0]["id"])
        self.assertIn("refusée", run_tool("close_mission", ctx, {"summary": "Trois articles écrits dans contenus/blog/, prêts à publier."}))
        self.db.set_task(c, status="done", result="ok")
        self.assertIn("clôturée", run_tool("close_mission", ctx, {"summary": "Trois articles écrits dans contenus/blog/, prêts à publier."}))
        m = next(x for x in missions.missions(self.db) if x["kind"] == "objectif")
        self.assertEqual((m["status"], m["pm"], m["to_check"]), ("done", True, True))
        self.assertIn("contenus/blog", m["pm_summary"])

    def test_spending_cap_blocks_mission(self):
        from ai_studio.runner import run_task
        self.cfg.data["missions"] = {"max_usd_per_mission": 0.5}
        oid = self.db.add_objective("Mission coûteuse")
        missions.assign_pm(self.db, "objectif", oid)
        t = self.db.add_task("Étape", "recherche", objective_id=oid)
        self.db.add_cost("recherche", t, "m", 1, 1, 0, 0.6)
        from test_core import FakeLLM
        from ai_studio.llm import BudgetGuard
        self.assertEqual(run_task(self.cfg, self.db, FakeLLM([]), BudgetGuard(self.cfg, self.db), self.db.task(t)), "blocked")
        self.assertIn("plafond de la mission", self.db.task(t)["result"])

    def test_orders_and_reviews_go_to_project_manager(self):
        from pathlib import Path
        from ai_studio import commerce
        from ai_studio.orchestrator import Studio
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")
        n = commerce.test_order(self.cfg, self.db, "Site vitrine", "proprio@studio.fr")
        self.assertTrue(missions.has_pm(self.db, "commande", n))
        oid = self.db.add_objective("Mission pilotée")
        missions.assign_pm(self.db, "objectif", oid)
        self.db.run("UPDATE tasks SET status='done' WHERE agent='chef_projet' AND objective_id=?", (oid,))
        Studio(self.cfg, self.db, None).schedule_reviews()
        self.assertEqual(self.db.one("SELECT agent FROM tasks WHERE objective_id=? AND status='pending'", (oid,))[0], "chef_projet")
        self.assertIsNone(self.db.one("SELECT 1 FROM tasks WHERE agent='pdg' AND title LIKE 'Revue de l''objectif #%' AND objective_id=?", (oid,)))


if __name__ == "__main__":
    unittest.main()
