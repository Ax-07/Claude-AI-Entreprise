"""Relecture juridique AVANT le propriétaire : renvoi à l'agent pour correction, plafond d'allers-retours, délai."""
import json
import os
import unittest
from datetime import timedelta
from pathlib import Path

from test_commerce import Base
from test_core import FakeLLM

from ai_studio import commerce, legal, notify
from ai_studio.agents import ROLES
from ai_studio.db import iso, now
from ai_studio.tools import ToolContext, run_tool


class LegalFlowTest(Base):
    def setUp(self):
        super().setUp()
        os.environ["STRIPE_SECRET_KEY"] = "rk_test_x"
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")

    def ctx_for(self, tid):
        t = self.db.task(tid)
        return ToolContext(self.cfg, self.db, t["agent"], t, list(ROLES))

    def submit(self, tid):
        out = run_tool("propose_payment_link", self.ctx_for(tid), {"offer": "Diagnostic SEO local", "reason": "vendre"})
        self.assertIn("en attente de validation", out)
        legal.queue_reviews(self.cfg, self.db)
        return self.db.one("SELECT * FROM approvals ORDER BY id DESC LIMIT 1")

    def verdict(self, a, v, notes="Ajouter « TVA non applicable, art. 293 B du CGI » dans la description."):
        t = self.db.one("SELECT * FROM tasks WHERE id=?", (a["review_task"],))
        return run_tool("legal_review", self.ctx_for(t["id"]), {"approval_id": a["id"], "verdict": v, "notes": notes})

    def test_runner_puts_request_in_review_before_owner_sees_it(self):
        from ai_studio.llm import BudgetGuard
        from ai_studio.runner import run_task
        tid = self.db.add_task("Créer le lien", "ventes")
        llm = FakeLLM([[{"type": "tool_use", "id": "1", "name": "propose_payment_link", "input": {"offer": "Diagnostic SEO local", "reason": "vendre"}}],
                       [{"type": "tool_use", "id": "2", "name": "complete_task", "input": {"result": "lien demandé"}}]])
        run_task(self.cfg, self.db, llm, BudgetGuard(self.cfg, self.db), self.db.task(tid))
        a = self.db.one("SELECT * FROM approvals")
        self.assertEqual(a["status"], "review")                                   # pas encore chez le propriétaire
        self.assertEqual(self.db.pending_approvals(), [])
        self.assertEqual(notify.check(self.cfg, self.db, {}), [])                  # aucune notification
        from ai_studio.server import approvals
        self.assertTrue(approvals(self.cfg, self.db)[0]["in_review"])

    def test_corrections_go_back_to_agent_then_owner(self):
        tid = self.db.add_task("Créer le lien", "ventes")
        a = self.submit(tid)
        out = self.verdict(a, "corrections")
        self.assertIn("renvoyée à ventes", out)
        a = self.db.approval(a["id"])
        self.assertEqual(a["status"], "rejected"); self.assertIn("Renvoyée par le Juridique", a["result"])
        fix = self.db.one("SELECT * FROM tasks WHERE title LIKE 'Corriger la demande%'")
        self.assertEqual((fix["agent"], fix["parent_id"]), ("ventes", tid))
        self.assertIn("TVA non applicable", fix["description"])
        self.assertEqual(self.db.pending_approvals(), [])
        a2 = self.submit(fix["id"])                                                # l'agent corrige et refait la demande
        self.assertEqual(a2["status"], "review")
        self.assertIn("transmise au propriétaire", self.verdict(a2, "ok", "Conforme."))
        p = self.db.pending_approvals()
        self.assertEqual([x["id"] for x in p], [a2["id"]]); self.assertIn("✅ Conforme", p[0]["review"])
        self.assertEqual(len(notify.check(self.cfg, self.db, {})), 1)              # notifiée une fois, à ce moment-là
        self.assertEqual(notify.check(self.cfg, self.db, {}), [])

    def test_round_limit_lets_owner_decide(self):
        tid = self.db.add_task("Créer le lien", "ventes")
        for _ in range(2):
            a = self.submit(tid)
            self.verdict(a, "corrections")
            tid = self.db.one("SELECT id FROM tasks WHERE title LIKE 'Corriger la demande%' ORDER BY id DESC")[0]
        a = self.submit(tid)
        out = self.verdict(a, "corrections")
        self.assertIn("c'est lui qui tranche", out)
        a = self.db.approval(a["id"])
        self.assertEqual(a["status"], "pending"); self.assertIn("le propriétaire tranche", a["review"])

    def test_failed_or_late_review_releases_with_mention(self):
        tid = self.db.add_task("Créer le lien", "ventes")
        a = self.submit(tid)
        self.db.set_task(a["review_task"], status="failed")
        legal.release_stale(self.cfg, self.db)
        a = self.db.approval(a["id"])
        self.assertEqual(a["status"], "pending"); self.assertIn("Relecture juridique non faite (tâche de relecture en échec)", a["review"])
        b = self.submit(self.db.add_task("Autre", "ventes"))
        self.db.run("UPDATE approvals SET created_at=? WHERE id=?", (iso(now() - timedelta(hours=4)), b["id"]))
        legal.release_stale(self.cfg, self.db)
        self.assertIn("pas d'avis après 3 h", self.db.approval(b["id"])["review"])

    def test_mission_with_request_in_review_is_not_stalled(self):
        from ai_studio import missions
        oid = self.db.add_objective("Vendre")
        kick = missions.assign_pm(self.db, "objectif", oid)
        self.db.set_task(kick, status="done")
        step = self.db.add_task("Lien", "ventes", parent_id=kick)
        self.submit(step)
        self.db.set_task(step, status="done")
        self.db.run("UPDATE tasks SET status='done' WHERE agent='juridique'")
        self.db.run("UPDATE approvals SET status='review'")
        self.assertIsNone(missions.relaunch_mission(self.db, "objectif", oid))       # elle attend la relecture


if __name__ == "__main__":
    unittest.main()
