"""Discussion directe avec le PDG depuis le tableau de bord (faux modèle : aucun coût)."""
import unittest

from ai_studio import chat
from test_core import Base, FakeLLM, tu


class ChatTest(Base):
    def tearDown(self):
        chat.LLM_FACTORY = None

    def test_question_then_mission(self):
        llm = FakeLLM([
            [tu("company_stats", {})],
            [{"type": "text", "text": "Aucune vente pour l'instant, 0 tâche en cours."}],
            [tu("create_objective", {"text": "Trouver 20 artisans lyonnais sans site web et préparer la prospection", "activity": "services"}),
             tu("delegate_task", {"agent": "recherche", "title": "Lister 20 artisans sans site", "description": "Lyon"})],
            [{"type": "text", "text": "C'est lancé : objectif créé et Labo sollicité."}],
        ])
        chat.LLM_FACTORY = lambda cfg: llm
        r = chat.send(self.cfg, self.db, "Où en est-on ?")
        self.assertEqual((r["text"], r["actions"]), ("Aucune vente pour l'instant, 0 tâche en cours.", []))
        self.assertGreater(r["cost_usd"], 0)
        r = chat.send(self.cfg, self.db, "Lance la prospection des artisans à Lyon")
        self.assertEqual(len(r["actions"]), 2)
        self.assertEqual(self.db.one("SELECT text, activity FROM objectives")[1], "services")
        t = self.db.one("SELECT agent, created_by FROM tasks")
        self.assertEqual((t[0], t[1]), ("recherche", "pdg"))
        # l'historique est repris comme contexte : 3e appel = 2 messages du propriétaire + 1 réponse
        third = llm.calls[2]["messages"]
        self.assertEqual([m["role"] for m in third], ["user", "assistant", "user"])
        self.assertEqual([m["role"] for m in chat.history(self.db)], ["owner", "pdg", "owner", "pdg"])
        self.assertIn("create_objective", [t["name"] for t in llm.calls[0]["tools"]])
        self.assertNotIn("send_email", [t["name"] for t in llm.calls[0]["tools"]])   # pas d'action externe directe

    def test_limits(self):
        chat.LLM_FACTORY = lambda cfg: FakeLLM([])
        with self.assertRaises(ValueError):
            chat.send(self.cfg, self.db, "   ")
        with self.assertRaises(ValueError):
            chat.send(self.cfg, self.db, "x" * 5000)
        self.db.add_cost("pdg", None, "m", 1, 1, 0, 0.6)                       # budget du jour dépassé (0,50 $)
        r = chat.send(self.cfg, self.db, "Bonjour")
        self.assertIn("budget", r["text"])
        from ai_studio.llm import LLMError
        def broken(cfg):
            raise LLMError("ANTHROPIC_API_KEY manquante")
        chat.LLM_FACTORY = broken
        self.db.run("DELETE FROM costs")
        self.assertIn("ANTHROPIC_API_KEY", chat.send(self.cfg, self.db, "Bonjour")["text"])


if __name__ == "__main__":
    unittest.main()
