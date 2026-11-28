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
            [tu("launch_mission", {"title": "Prospection des artisans lyonnais", "instructions": "20 artisans sans site, email Site vitrine",
                                   "project_manager": True, "activity": "services"}),
             tu("launch_mission", {"title": "Point sur les avis Google", "instructions": "Synthèse en 10 lignes", "agent": "recherche"})],
            [{"type": "text", "text": "Mission #1 lancée avec un chef de projet, mission #2 confiée au Labo."}],
        ])
        chat.LLM_FACTORY = lambda cfg: llm
        r = chat.send(self.cfg, self.db, "Où en est-on ?")
        self.assertEqual((r["text"], r["actions"]), ("Aucune vente pour l'instant, 0 tâche en cours.", []))
        self.assertGreater(r["cost_usd"], 0)
        t1 = r["thread"]
        r = chat.send(self.cfg, self.db, "Lance la prospection des artisans à Lyon", t1)
        self.assertEqual(len(r["actions"]), 2)
        objs = self.db.q("SELECT id, activity, project_manager FROM objectives ORDER BY id")
        self.assertEqual([(o["activity"], o["project_manager"]) for o in objs], [("services", "chef_projet"), (None, None)])
        tasks = self.db.q("SELECT agent, objective_id FROM tasks ORDER BY id")
        self.assertEqual([(t["agent"], t["objective_id"]) for t in tasks], [("chef_projet", objs[0]["id"]), ("recherche", objs[1]["id"])])
        self.assertIn("Mission #", r["actions"][0])                             # chaque demande devient une mission numérotée
        # l'historique est repris comme contexte : 3e appel = 2 messages du propriétaire + 1 réponse
        third = llm.calls[2]["messages"]
        self.assertEqual([m["role"] for m in third], ["user", "assistant", "user"])
        self.assertEqual([m["role"] for m in chat.history(self.db, t1)], ["owner", "pdg", "owner", "pdg"])
        self.assertEqual(chat.threads(self.db)[0]["title"], "Où en est-on ?")       # titre = premier message
        names = [t["name"] for t in llm.calls[0]["tools"]]
        self.assertIn("launch_mission", names); self.assertNotIn("delegate_task", names)   # pas de tâche orpheline
        self.assertNotIn("send_email", [t["name"] for t in llm.calls[0]["tools"]])   # pas d'action externe directe

    def test_separate_threads(self):
        llm = FakeLLM([[{"type": "text", "text": "R1"}], [{"type": "text", "text": "R2"}], [{"type": "text", "text": "R3"}]])
        chat.LLM_FACTORY = lambda cfg: llm
        a = chat.send(self.cfg, self.db, "Sujet A : la prospection")["thread"]
        b = chat.new_thread(self.db)
        chat.send(self.cfg, self.db, "Sujet B : les factures", b)
        self.assertEqual(len(llm.calls[1]["messages"]), 1)                       # nouvelle discussion : rien de A
        chat.send(self.cfg, self.db, "Et la suite pour A ?", a)
        self.assertEqual([m["role"] for m in llm.calls[2]["messages"]], ["user", "assistant", "user"])
        ts = chat.threads(self.db)
        self.assertEqual([t["id"] for t in ts], [a, b])                         # la plus récemment utilisée d'abord
        self.assertEqual((ts[0]["messages"], ts[1]["title"]), (4, "Sujet B : les factures"))
        with self.assertRaises(ValueError):
            chat.send(self.cfg, self.db, "?", 999)

    def test_legacy_messages_grouped(self):
        from ai_studio.db import DB
        self.db.run("INSERT INTO chat(role, text, created_at) VALUES ('owner','ancien','2026-11-27T20:00:00+00:00')")
        DB(self.cfg.db_path).conn.close()                                      # réouverture : migration
        db2 = DB(self.cfg.db_path)
        self.assertEqual([t["title"] for t in chat.threads(db2)], ["Première discussion"])
        self.assertEqual(chat.history(db2)[0]["text"], "ancien")

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
