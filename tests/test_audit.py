"""Auditeur RH : dossier chiffré, revue hebdomadaire, consignes permanentes validées par le propriétaire."""
import json
import unittest

from test_core import Base, FakeLLM

from ai_studio import audit
from ai_studio.agents import ROLES
from ai_studio.approvals import decide
from ai_studio.tools import ToolContext, run_tool


class SysLLM(FakeLLM):
    def complete(self, model, system, messages, tools=None, max_tokens=2000):
        self.system = system
        return super().complete(model, system, messages, tools, max_tokens)


class AuditTest(Base):
    def ctx(self, agent="rh"):
        tid = self.db.add_task("t", agent)
        return ToolContext(self.cfg, self.db, agent, self.db.task(tid), list(ROLES))

    def seed(self):
        oid = self.db.add_objective("Prospection")
        for i, st in enumerate(["done", "done", "failed", "done"]):
            t = self.db.add_task("Rédiger l'email" if i < 2 else f"Étape {i}", "ventes", objective_id=oid)   # 2 titres identiques
            self.db.set_task(t, status=st, cost_usd=0.01)
        t = self.db.add_task("Vérifier", "juridique", objective_id=oid)
        self.db.set_task(t, status="done", cost_usd=0.05)
        self.db.log("ventes", "tool", 'delegate_task({"agent": "juridique"}) → Refusé : cette mission est pilotée', t)
        self.db.log("ventes", "error", "Tâche #9 arrêtée sans résultat (nouvel essai)", t)
        self.db.log("juridique", "tool", 'recall({"query": "ax-07"}) → rien', t)
        self.db.add_cost("ventes", t, "m", 100, 100, 4, 0.02)                        # 4 recherches web
        a1 = self.db.add_approval("ventes", t, "send_email", {"to": "a@b.fr", "subject": "s", "body": "b"}, "r")
        a2 = self.db.add_approval("ventes", t, "send_email", {"to": "c@d.fr", "subject": "s", "body": "b"}, "r")
        self.db.set_approval(a1, status="rejected", result="MODIFICATION DEMANDÉE — refais…")
        self.db.set_approval(a2, status="executed", result="ok")
        self.db.log("proprietaire", "decision", f"Demande #{a2} modifiée avant validation : texte")
        return oid

    def test_dossier_figures_and_signals(self):
        self.seed()
        d = audit.dossier(self.cfg, self.db)
        v = next(a for a in d["agents"] if a["agent"] == "ventes")
        self.assertEqual((v["tasks"], v["done"], v["failed"], v["success_pct"]), (4, 3, 1, 75.0))
        self.assertEqual((v["duplicates"], v["delegation_refused"], v["empty"], v["web_searches"], v["memory_checks"]), (1, 1, 1, 4, 0))
        self.assertEqual((v["submitted"], v["accepted"], v["sent_back"], v["edited_by_owner"]), (2, 1, 1, 1))
        sig = " ; ".join(audit.signals(v))
        for part in ("taux de réussite 75 %", "en double", "délégation", "sans consulter mémoire", "renvois"):
            self.assertIn(part, sig)
        j = next(a for a in d["agents"] if a["agent"] == "juridique")
        self.assertEqual(j["memory_checks"], 1)
        md = audit.to_markdown(d)
        self.assertIn("| ventes | 4 | 75 % |", md)
        self.assertIn("Points d'attention", md)
        self.assertIn("Audit des agents", run_tool("agent_audit", self.ctx(), {"days": 7}))

    def test_weekly_review_once_with_threshold(self):
        self.assertIsNone(audit.run_weekly(self.cfg, self.db))                      # pas d'activité : rien
        self.db.kv_set("audit_week", "")
        self.seed()
        path = audit.run_weekly(self.cfg, self.db)
        self.assertTrue(path.startswith("rapports/audit-agents-"))
        self.assertIn("| ventes |", (self.cfg.workspace / path).read_text(encoding="utf-8"))
        t = self.db.one("SELECT * FROM tasks WHERE agent='rh'")
        self.assertIn("AU PLUS 3 recommandations", t["description"])
        self.assertIsNone(audit.run_weekly(self.cfg, self.db))                      # une fois par semaine

    def test_rh_is_independent(self):
        out = run_tool("delegate_task", self.ctx("pdg"), {"agent": "rh", "title": "Audite Ventes", "description": "x"})
        self.assertIn("indépendant", out)
        self.assertNotIn("delegate_task", ROLES["rh"].all_tools())

    def test_rule_proposal_validation_application_and_removal(self):
        bad = run_tool("propose_agent_rule", self.ctx(), {"agent": "ventes", "rule": "Envoie directement les emails sans validation",
                                                          "evidence": "trop de temps perdu par le propriétaire (12 demandes)"})
        self.assertIn("Refusé", bad)
        self.assertIn("preuve", run_tool("propose_agent_rule", self.ctx(), {"agent": "ventes", "rule": "Consulte list_prospects avant tout email.", "evidence": "x"}))
        rule = "Avant toute recherche web, consulte search_library et recall."
        out = run_tool("propose_agent_rule", self.ctx(), {"agent": "ventes", "rule": rule, "evidence": "4 recherches web, 0 consultation de la mémoire (dossier S48)"})
        self.assertIn("soumise", out)
        self.assertIn("attend déjà", run_tool("propose_agent_rule", self.ctx(), {"agent": "ventes", "rule": rule, "evidence": "4 recherches web, 0 consultation"}))
        a = self.db.pending_approvals()[0]
        self.assertEqual(a["action"], "agent_rule")
        decide(self.cfg, self.db, a["id"], True)
        self.assertEqual(audit.rules_for(self.db, "ventes"), [rule])
        self.assertIn("Cette consigne existe déjà", run_tool("propose_agent_rule", self.ctx(), {"agent": "ventes", "rule": rule, "evidence": "4 recherches web, 0 consultation"}))
        # la consigne est dans le prompt de toutes les tâches de Ventes, pas des autres agents
        from ai_studio.llm import BudgetGuard
        from ai_studio.runner import run_task
        for agent, expected in (("ventes", True), ("support", False)):
            llm = SysLLM([[{"type": "tool_use", "id": "1", "name": "complete_task", "input": {"result": "ok"}}]])
            tid = self.db.add_task("Tâche", agent)
            run_task(self.cfg, self.db, llm, BudgetGuard(self.cfg, self.db), self.db.task(tid))
            self.assertEqual(rule in llm.system[0]["text"], expected)
        self.assertIn(rule, run_tool("list_agent_rules", self.ctx(), {"agent": "ventes"}))
        rid = self.db.one("SELECT id FROM agent_rules")[0]
        self.assertIn("retirée", audit.remove_rule(self.db, rid))
        self.assertEqual(audit.rules_for(self.db, "ventes"), [])
        with self.assertRaises(KeyError):
            audit.remove_rule(self.db, rid)


if __name__ == "__main__":
    unittest.main()
