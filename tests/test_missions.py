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
        self.assertEqual((m["title"], m["test"], m["state"]), (f"Commande n°{n} · Site vitrine", True, "À livrer"))
        missions.verify(self.cfg, self.db, "commande", n, "issue", "Le site n'a pas de page contact")
        self.assertIn("page contact", commerce.get_order(self.db, n)["owner_notes"])
        from ai_studio.server import events
        ev = events(self.cfg, self.db)
        self.assertTrue(any(e["mission"] and e["mission"]["label"].startswith(f"Commande n°{n}") for e in ev))


if __name__ == "__main__":
    unittest.main()
