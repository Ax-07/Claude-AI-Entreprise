"""Autorisations des outils imposées par le code (audit SEC-01) : un agent n'exécute que les outils de son rôle.

La liste d'outils envoyée au modèle n'est qu'une indication : une réponse inattendue ou manipulée (prompt
injection dans un email, une page web…) ne doit jamais pouvoir déclencher un outil hors du périmètre de l'agent.
"""
import os
import unittest.mock

from ai_studio import chat
from ai_studio.agents import ROLES
from ai_studio.runner import run_task
from ai_studio.tools import REGISTRY, ToolContext, allowed_tools, run_tool
from test_core import Base, FakeLLM, tu


class PermissionsTest(Base):
    def ctx(self, agent, allowed=None):
        tid = self.db.add_task("t", agent if agent in ROLES else "pdg")
        return ToolContext(self.cfg, self.db, agent, self.db.task(tid), list(ROLES), allowed=allowed)

    def spy(self, name):
        """Remplace la fonction de l'outil par un espion : on vérifie qu'elle n'est jamais appelée."""
        calls = []
        p = unittest.mock.patch.object(REGISTRY[name], "func", lambda ctx, **kw: calls.append(kw) or "exécuté")
        p.start(); self.addCleanup(p.stop)
        return calls

    def test_tool_outside_role_is_refused_and_not_executed(self):
        self.assertNotIn("send_email", ROLES["recherche"].granted_tools())     # prémisse du test
        calls = self.spy("send_email")
        out = run_tool("send_email", self.ctx("recherche"), {"to": "a@b.fr", "subject": "s", "body": "b", "reason": "r"})
        self.assertIn("Refusé", out)
        self.assertEqual(calls, [])
        self.assertEqual(self.db.pending_approvals(), [])

    def test_alias_does_not_bypass_permissions(self):
        calls = self.spy("send_email")
        self.assertIn("Refusé", run_tool("draft_email", self.ctx("atelier"), {"to": "a@b.fr"}))
        self.assertEqual(calls, [])
        self.assertIn("exécuté", run_tool("draft_email", self.ctx("ventes"), {"to": "a@b.fr"}))   # ancien nom, rôle autorisé

    def test_unknown_agent_gets_nothing(self):
        calls = self.spy("list_files")
        self.assertIn("Refusé", run_tool("list_files", self.ctx("intrus"), {}))
        self.assertEqual(calls, [])

    def test_every_role_can_use_its_own_tools(self):
        for r in ROLES.values():
            allowed = allowed_tools(ToolContext(self.cfg, self.db, r.name, None, []))
            self.assertTrue(set(r.all_tools()) <= allowed, r.name)
            self.assertEqual("brave_search" in allowed, r.web_search, r.name)

    def test_explicit_list_restricts_even_role_tools(self):
        self.assertIn("save_offer", ROLES["pdg"].all_tools())
        calls = self.spy("save_offer")
        self.assertIn("Refusé", run_tool("save_offer", self.ctx("pdg", allowed=frozenset(chat.TOOLS)), {}))
        self.assertEqual(calls, [])

    def test_unconfigured_tool_is_refused(self):
        os.environ.pop("BRAVE_API_KEY", None)
        t = REGISTRY["brave_search"]
        with unittest.mock.patch.object(t, "requires_env", ("BRAVE_API_KEY",)):
            self.assertIn("pas configuré", run_tool("brave_search", self.ctx("recherche"), {"query": "x"}))

    def test_refusal_is_logged(self):
        run_tool("save_offer", self.ctx("support"), {})
        ev = self.db.q("SELECT agent, kind, message FROM events WHERE kind='refus'")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["agent"], "support")
        self.assertIn("save_offer", ev[0]["message"])

    def test_runner_refuses_tool_the_model_was_not_offered(self):
        calls = self.spy("send_email")
        tid = self.db.add_task("Étude", "recherche", "x")
        llm = FakeLLM([
            [tu("send_email", {"to": "cible@exemple.fr", "subject": "s", "body": "b", "reason": "injection"})],
            [tu("complete_task", {"result": "Étude terminée sans envoi."})],
        ])
        self.assertEqual(run_task(self.cfg, self.db, llm, self.guard, self.db.task(tid)), "done")
        self.assertEqual(calls, [])
        offered = {t.get("name") for t in llm.calls[0]["tools"]}
        self.assertNotIn("send_email", offered)
        self.assertIn("Refusé", str(llm.calls[1]["messages"][-1]["content"]))

    def test_chat_pdg_cannot_use_tools_outside_discussion(self):
        calls = self.spy("save_offer")
        llm = FakeLLM([
            [tu("save_offer", {"name": "Offre pirate", "price_eur": 1, "summary": "s", "deliverable": "d", "delay_hours": 1})],
            [{"type": "text", "text": "Je ne peux pas faire ça d'ici."}],
        ])
        chat.LLM_FACTORY = lambda cfg: llm
        self.addCleanup(setattr, chat, "LLM_FACTORY", None)
        chat.send(self.cfg, self.db, "Crée une offre à 1 €")
        self.assertEqual(calls, [])
        self.assertIn("Refusé", str(llm.calls[1]["messages"][-1]["content"]))


if __name__ == "__main__":
    unittest.main()
