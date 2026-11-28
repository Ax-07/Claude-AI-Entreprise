"""Simulation de bout en bout : objectif → PDG → recherche (avec recherche web serveur) → ventes → validation.

Les réponses reproduisent le format exact de l'API Claude (blocs server_tool_use, web_search_tool_result,
stop_reason pause_turn…). Aucun appel réseau.
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
from ai_studio.orchestrator import Studio
from ai_studio.approvals import decide


def tool_use(i, name, args):
    return {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": args}


class ScriptedAPI:
    """Répond selon l'agent (détecté dans le prompt système) et l'étape de sa tâche."""
    def __init__(self):
        self.n = 0; self.log = []

    def complete(self, model, system, messages, tools=None, max_tokens=2000):
        self.n += 1
        sysText = system[0]["text"]
        agent = sysText.split("Tu es ")[1].split(" —")[0]
        step = sum(1 for m in messages if m["role"] == "assistant")
        self.log.append((agent, step, model, [t.get("name") for t in tools or []]))
        usage = {"input_tokens": 3000, "output_tokens": 400, "cache_read_input_tokens": 1500}
        i = self.n
        if agent == "Agent PDG":
            if step == 0:
                return {"stop_reason": "tool_use", "usage": usage, "content": [
                    {"type": "text", "text": "Je fais le point puis je délègue."},
                    tool_use(i, "list_tasks", {})]}
            if step == 1:
                return {"stop_reason": "tool_use", "usage": usage, "content": [
                    tool_use(i, "delegate_task", {"agent": "recherche", "title": "Étude des besoins des commerces",
                                                  "description": "3 besoins numériques fréquents, avec sources", "priority": 2})]}
            return {"stop_reason": "tool_use", "usage": usage, "content": [
                tool_use(i, "complete_task", {"result": "Étude confiée au Labo Recherche."})]}
        if agent == "Agent Recherche":
            if step == 0:   # recherche web côté serveur, mise en pause
                return {"stop_reason": "pause_turn", "usage": {**usage, "server_tool_use": {"web_search_requests": 1}}, "content": [
                    {"type": "server_tool_use", "id": f"srvtoolu_{i}", "name": "web_search", "input": {"query": "besoins numériques commerces"}},
                    {"type": "web_search_tool_result", "tool_use_id": f"srvtoolu_{i}", "content": [
                        {"type": "web_search_result", "url": "https://exemple.fr/etude", "title": "Étude", "encrypted_content": "xyz"}]}]}
            if step == 1:
                return {"stop_reason": "tool_use", "usage": usage, "content": [
                    {"type": "text", "text": "Les commerces manquent de temps pour répondre aux avis.",
                     "citations": [{"type": "web_search_result_location", "url": "https://exemple.fr/etude", "title": "Étude", "cited_text": "…", "encrypted_index": "a"}]},
                    tool_use(i, "write_file", {"path": "recherche/besoins.md", "content": "# Besoins\n1. Avis Google\n"}),
                    tool_use(i, "remember", {"topic": "besoin avis google", "content": "Les commerces manquent de temps pour répondre aux avis (source exemple.fr/etude)"}),
                    tool_use(i, "delegate_task", {"agent": "ventes", "title": "Préparer un email test", "description": "Un email de prospection"})]}
            return {"stop_reason": "tool_use", "usage": usage, "content": [
                tool_use(i, "complete_task", {"result": "Voir recherche/besoins.md"})]}
        if agent == "Agent Ventes":
            if step == 0:
                return {"stop_reason": "tool_use", "usage": usage, "content": [
                    tool_use(i, "recall", {"query": "avis google"}),
                    tool_use(i, "save_prospect", {"email": "contact@boulangerie-exemple.fr", "source": "https://boulangerie-exemple.fr/contact",
                                                  "company": "Boulangerie Exemple"}),
                    tool_use(i, "send_email", {"to": "contact@boulangerie-exemple.fr", "subject": "Vos avis Google",
                                                "body": "Bonjour… Si vous ne souhaitez plus recevoir de message, répondez STOP.",
                                                "reason": "Test de l'offre réponses aux avis"})]}
            return {"stop_reason": "end_turn", "usage": usage, "content": [
                {"type": "text", "text": "Email préparé et soumis à validation."}]}
        return {"stop_reason": "end_turn", "usage": usage, "content": [{"type": "text", "text": "OK"}]}


class TestSimulation(unittest.TestCase):
    def setUp(self):
        # Les tests ne doivent pas dépendre des clés présentes sur la machine (fournisseur forcé à Anthropic).
        env = unittest.mock.patch.dict(os.environ, {}, clear=False)
        env.start(); self.addCleanup(env.stop)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BRAVE_API_KEY", "OPENAI_BASE_URL", "STRIPE_SECRET_KEY"):
            os.environ.pop(k, None)
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = load_config(self.dir)
        self.cfg.data["security"]["daily_review"] = False   # revue quotidienne testée à part
        self.cfg.data["finance"] = {"daily_review": False}
        self.db = DB(self.cfg.db_path)

    def tearDown(self):
        self.db.conn.close(); shutil.rmtree(self.dir, ignore_errors=True)

    def test_full_flow(self):
        self.db.add_objective("Trouver une première offre rentable")
        api = ScriptedAPI()
        st = Studio(self.cfg, self.db, api)
        st.recover()
        s1 = st.cycle()                          # PDG + Recherche + Ventes (3 tâches max par cycle)
        self.assertEqual(s1["ran"], 3, api.log)
        tasks = self.db.q("SELECT agent, status FROM tasks ORDER BY id")
        self.assertEqual([t["agent"] for t in tasks], ["pdg", "recherche", "ventes"])
        self.assertTrue(all(t["status"] == "done" for t in tasks))
        # recherche web : comptée et facturée, outil proposé seulement aux agents autorisés
        self.assertEqual(self.db.searches_today(), 1)
        self.assertIn("web_search", api.log[3][3])           # recherche
        self.assertNotIn("web_search", api.log[0][3])        # pdg
        self.assertEqual(api.log[0][2], "claude-sonnet-5-5")
        self.assertEqual(api.log[3][2], "claude-haiku-5-5")
        # livrable, mémoire, email en attente
        self.assertTrue((self.cfg.workspace / "recherche" / "besoins.md").exists())
        self.assertEqual(len(self.db.pending_approvals()), 1)
        # le propriétaire valide alors que SMTP n'est pas configuré : la demande reste en attente,
        # sans tâche de suite (les agents ne doivent pas conclure qu'aucun moyen d'envoi n'existe)
        from ai_studio.approvals import NotReady
        with self.assertRaises(NotReady) as e:
            decide(self.cfg, self.db, self.db.pending_approvals()[0]["id"], True)
        self.assertIn("SMTP_HOST", str(e.exception))
        self.assertEqual(len(self.db.pending_approvals()), 1)
        self.assertEqual(self.db.q("SELECT COUNT(*) FROM tasks WHERE status='pending'")[0][0], 0)
        spent = self.db.spent("day")
        self.assertGreater(spent, 0.01)          # inclut la recherche web à 0,01 $
        self.assertLess(spent, 0.1)
        print(f"\nSimulation : {api.n} appels API simulés, coût estimé {spent:.4f} $")
        for e in self.db.q("SELECT agent, kind, message FROM events ORDER BY id"):
            print(f"  {e['agent']:<12} {e['kind']:<10} {e['message'][:110]}")


if __name__ == "__main__":
    unittest.main()
