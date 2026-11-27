"""Tests sans appel réseau : un faux modèle joue un scénario d'outils.

Lancer : python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from ai_studio.config import load_config
from ai_studio.db import DB
from ai_studio.llm import BudgetExceeded, BudgetGuard, compute_cost, models_for
from ai_studio.orchestrator import Studio
from ai_studio.runner import run_task
from ai_studio.approvals import decide
from ai_studio.tools import REGISTRY
from ai_studio.agents import ROLES


def tu(name, args, i=[0]):
    i[0] += 1
    return {"type": "tool_use", "id": f"tu_{i[0]}", "name": name, "input": args}


class FakeLLM:
    """Renvoie une suite de réponses scriptées et garde les requêtes reçues."""
    def __init__(self, script, usage=None):
        self.script = list(script); self.calls = []
        self.usage = usage or {"input_tokens": 1000, "output_tokens": 200}

    def complete(self, model, system, messages, tools=None, max_tokens=2000):
        self.calls.append({"model": model, "messages": json.loads(json.dumps(messages)), "tools": tools})
        content = self.script.pop(0) if self.script else [{"type": "text", "text": "fin"}]
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        return {"content": content, "stop_reason": stop, "usage": dict(self.usage)}


class Base(unittest.TestCase):
    def setUp(self):
        # Les tests ne doivent pas dépendre des clés présentes sur la machine (fournisseur forcé à Anthropic).
        env = unittest.mock.patch.dict(os.environ, {}, clear=False)
        env.start(); self.addCleanup(env.stop)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BRAVE_API_KEY", "OPENAI_BASE_URL", "STRIPE_SECRET_KEY"):
            os.environ.pop(k, None)
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "config.json").write_text(json.dumps({"budget": {"monthly_usd": 1.0, "daily_usd": 0.5, "per_task_usd": 0.05}}))
        self.cfg = load_config(self.dir)
        self.db = DB(self.cfg.db_path)
        self.guard = BudgetGuard(self.cfg, self.db)

    def tearDown(self):
        self.db.conn.close()
        shutil.rmtree(self.dir, ignore_errors=True)


class TestTools(Base):
    def test_all_role_tools_exist(self):
        for r in ROLES.values():
            for n in r.all_tools():
                self.assertIn(n, REGISTRY, f"{r.name} : outil manquant {n}")

    def test_cost_haiku(self):
        usd, i, o, s = compute_cost(self.cfg["prices"], "claude-haiku-5-5", {"input_tokens": 50_000, "output_tokens": 100_000})
        self.assertAlmostEqual(usd, 0.055, places=5)
        usd, *_ = compute_cost(self.cfg["prices"], "claude-haiku-5-5", {"input_tokens": 200_000, "output_tokens": 0})
        self.assertAlmostEqual(usd, 0.10, places=5)      # tarif long contexte au-delà de 100K
        usd, *_ = compute_cost(self.cfg["prices"], "claude-sonnet-5-5", {"input_tokens": 1000, "output_tokens": 0, "server_tool_use": {"web_search_requests": 2}})
        self.assertAlmostEqual(usd, 0.002 + 0.02, places=5)


class TestRunner(Base):
    def test_task_with_tools_completes(self):
        tid = self.db.add_task("Étudier un marché", "recherche", "Fais une étude")
        llm = FakeLLM([
            [{"type": "text", "text": "Je commence."}, tu("remember", {"topic": "marché test", "content": "info clé"})],
            [tu("write_file", {"path": "recherche/etude.md", "content": "# Étude"})],
            [tu("complete_task", {"result": "Étude dans recherche/etude.md"})],
        ])
        status = run_task(self.cfg, self.db, llm, self.guard, self.db.task(tid))
        self.assertEqual(status, "done")
        t = self.db.task(tid)
        self.assertIn("etude.md", t["result"])
        self.assertTrue((self.cfg.workspace / "recherche" / "etude.md").exists())
        self.assertEqual(len(self.db.recall("marché")), 1)
        self.assertGreater(t["cost_usd"], 0)
        # le modèle a reçu le résultat de l'outil dans le message suivant
        last_user = llm.calls[1]["messages"][-1]
        self.assertEqual(last_user["content"][0]["type"], "tool_result")
        # recherche web proposée à l'agent recherche
        self.assertTrue(any(x.get("name") == "web_search" for x in llm.calls[0]["tools"]))

    def test_sandbox_blocks_escape(self):
        tid = self.db.add_task("x", "marketing")
        llm = FakeLLM([[tu("write_file", {"path": "../../evil.txt", "content": "x"})], [tu("complete_task", {"result": "ok"})]])
        run_task(self.cfg, self.db, llm, self.guard, self.db.task(tid))
        self.assertFalse((self.dir.parent / "evil.txt").exists())
        out = llm.calls[1]["messages"][-1]["content"][0]["content"]
        self.assertIn("hors de l'espace de travail", out)

    def test_email_goes_to_approval_and_followup(self):
        tid = self.db.add_task("Prospecter", "ventes")
        llm = FakeLLM([[tu("send_email", {"to": "contact@exemple.fr", "subject": "Bonjour", "body": "…", "reason": "test"})],
                       [tu("complete_task", {"result": "email préparé"})]])
        run_task(self.cfg, self.db, llm, self.guard, self.db.task(tid))
        pend = self.db.pending_approvals()
        self.assertEqual(len(pend), 1)
        msg = decide(self.cfg, self.db, pend[0]["id"], approve=False, note="pas maintenant")
        self.assertIn("REFUSÉ", msg)
        follow = self.db.q("SELECT * FROM tasks WHERE created_by='proprietaire'")
        self.assertEqual(len(follow), 1)
        self.assertEqual(follow[0]["agent"], "ventes")

    def test_delegation_depth_and_unknown_agent(self):
        tid = self.db.add_task("Planifier", "pdg")
        llm = FakeLLM([[tu("delegate_task", {"agent": "inconnu", "title": "a", "description": "b"}),
                        tu("delegate_task", {"agent": "recherche", "title": "Étude", "description": "b"})],
                       [tu("complete_task", {"result": "plan"})]])
        run_task(self.cfg, self.db, llm, self.guard, self.db.task(tid))
        self.assertEqual(llm.calls[0]["model"], models_for(self.cfg)["strong"])
        child = self.db.q("SELECT * FROM tasks WHERE parent_id=?", (tid,))
        self.assertEqual(len(child), 1)
        self.assertEqual(child[0]["depth"], 1)

    def test_per_task_budget_stops_loop(self):
        tid = self.db.add_task("Boucle", "data")
        big = {"input_tokens": 100_000, "output_tokens": 2000}           # ~0.011 $ par appel en Haiku
        llm = FakeLLM([[tu("recall", {"query": "x"})]] * 20, usage=big)
        status = run_task(self.cfg, self.db, llm, self.guard, self.db.task(tid))
        self.assertEqual(status, "done")
        self.assertIn("Plafond par tâche", self.db.task(tid)["result"])
        self.assertLess(len(llm.calls), 10)

    def test_daily_budget_pauses_studio(self):
        self.db.add_cost("pdg", None, "claude-haiku-5-5", 0, 0, 0, 0.6)
        self.db.add_task("t", "data")
        st = Studio(self.cfg, self.db, FakeLLM([]))
        s = st.cycle()
        self.assertIn("jour", s["paused"])
        self.assertEqual(s["ran"], 0)


class TestStudio(Base):
    def test_objective_creates_ceo_review_and_runs(self):
        self.db.add_objective("Trouver 3 idées d'offres")
        llm = FakeLLM([[tu("delegate_task", {"agent": "recherche", "title": "Idées", "description": "3 idées"})],
                       [tu("complete_task", {"result": "1 tâche confiée"})],
                       [tu("complete_task", {"result": "3 idées"})]])
        st = Studio(self.cfg, self.db, llm)
        s = st.cycle()
        self.assertEqual(s["reviews"], 1)
        self.assertEqual(s["ran"], 2)            # revue du PDG + tâche de recherche dans le même cycle
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM tasks WHERE status='done'")[0], 2)
        self.assertEqual(st.schedule_reviews(), 0)   # pas de nouvelle revue avant le délai


if __name__ == "__main__":
    unittest.main()
