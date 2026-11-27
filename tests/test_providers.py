"""Tests du fournisseur OpenAI (traduction des formats) et de l'outil Brave Search — sans réseau."""
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from ai_studio.config import load_config
from ai_studio.db import DB
from ai_studio.llm import (BudgetGuard, OpenAIClient, compute_cost, from_openai_response, make_llm, models_for,
                           resolve_provider, to_openai_messages, to_openai_tools)
from ai_studio.runner import run_task
from ai_studio.tools import core as core_tools


class Base(unittest.TestCase):
    def setUp(self):
        env = unittest.mock.patch.dict(os.environ, {}, clear=False)
        env.start(); self.addCleanup(env.stop)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BRAVE_API_KEY", "OPENAI_BASE_URL"):
            os.environ.pop(k, None)
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def make(self, cfg: dict | None = None):
        (self.dir / "config.json").write_text(json.dumps(cfg or {}))
        self.cfg = load_config(self.dir)
        self.db = DB(self.cfg.db_path)
        self.addCleanup(self.db.conn.close)
        self.guard = BudgetGuard(self.cfg, self.db)


class TestProviderChoice(Base):
    def test_auto_picks_provider_from_keys(self):
        self.make()
        os.environ["OPENAI_API_KEY"] = "sk-test"
        self.assertEqual(resolve_provider(self.cfg), "openai")
        self.assertEqual(models_for(self.cfg), {"default": "gpt-6-luna", "strong": "gpt-6.1-terra"})
        self.assertIsInstance(make_llm(self.cfg), OpenAIClient)
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        self.assertEqual(resolve_provider(self.cfg), "anthropic")
        self.assertEqual(models_for(self.cfg)["default"], "claude-haiku-5-5")

    def test_explicit_provider_and_old_config_format(self):
        self.make({"provider": "anthropic", "models": {"default": "claude-x", "strong": "claude-y"}})
        os.environ["OPENAI_API_KEY"] = "sk-test"
        self.assertEqual(resolve_provider(self.cfg), "anthropic")
        self.assertEqual(models_for(self.cfg), {"default": "claude-x", "strong": "claude-y"})

    def test_openai_cost_uses_cached_price(self):
        self.make()
        usd, i, o, s = compute_cost(self.cfg["prices"], "gpt-6-luna",
                                    {"input_tokens": 900_000, "cache_read_input_tokens": 100_000, "output_tokens": 100_000})
        self.assertAlmostEqual(usd, 0.09 + 0.001 + 0.05, places=6)


class TestOpenAIFormat(unittest.TestCase):
    def test_messages_roundtrip(self):
        system = [{"type": "text", "text": "Tu es l'agent.", "cache_control": {"type": "ephemeral"}}]
        msgs = [
            {"role": "user", "content": "Tâche #1"},
            {"role": "assistant", "content": [{"type": "text", "text": "Je cherche."},
                                              {"type": "tool_use", "id": "call_1", "name": "recall", "input": {"query": "x"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "rien"},
                                         {"type": "text", "text": "Limite d'étapes atteinte."}]},
        ]
        out = to_openai_messages(system, msgs)
        self.assertEqual(out[0], {"role": "system", "content": "Tu es l'agent."})
        self.assertEqual(out[2]["tool_calls"][0]["function"], {"name": "recall", "arguments": '{"query": "x"}'})
        self.assertEqual(out[3], {"role": "tool", "tool_call_id": "call_1", "content": "rien"})
        self.assertEqual(out[4]["role"], "user")

    def test_server_tools_are_dropped(self):
        tools = [{"name": "recall", "description": "d", "input_schema": {"type": "object", "properties": {}}},
                 {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
        self.assertEqual([t["function"]["name"] for t in to_openai_tools(tools)], ["recall"])

    def test_response_translation(self):
        resp = {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "call_9", "type": "function", "function": {"name": "complete_task", "arguments": '{"result": "ok"}'}}]}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "prompt_tokens_details": {"cached_tokens": 200}}}
        r = from_openai_response(resp)
        self.assertEqual(r["stop_reason"], "tool_use")
        self.assertEqual(r["content"], [{"type": "tool_use", "id": "call_9", "name": "complete_task", "input": {"result": "ok"}}])
        self.assertEqual(r["usage"], {"input_tokens": 1000, "cache_read_input_tokens": 200, "output_tokens": 80})


class TestOpenAIEndToEnd(Base):
    def test_task_runs_through_openai_client(self):
        self.make()
        os.environ["OPENAI_API_KEY"] = "sk-test"
        sent = []
        replies = [
            {"choices": [{"finish_reason": "tool_calls", "message": {"content": "Je note.", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "remember", "arguments": '{"topic": "t", "content": "c"}'}}]}}],
             "usage": {"prompt_tokens": 500, "completion_tokens": 40}},
            {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "c2", "type": "function", "function": {"name": "complete_task", "arguments": '{"result": "fini"}'}}]}}],
             "usage": {"prompt_tokens": 600, "completion_tokens": 20}},
        ]

        def transport(url, body, headers, timeout):
            sent.append((url, body, headers))
            return replies.pop(0)

        client = OpenAIClient(transport=transport)
        tid = self.db.add_task("Tester", "data")
        status = run_task(self.cfg, self.db, client, self.guard, self.db.task(tid))
        self.assertEqual(status, "done")
        self.assertEqual(self.db.task(tid)["result"], "fini")
        url, body, headers = sent[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(body["model"], "gpt-6-luna")
        self.assertEqual(headers["authorization"], "Bearer sk-test")
        self.assertIn("max_completion_tokens", body)
        self.assertEqual(sent[1][1]["messages"][-1], {"role": "tool", "tool_call_id": "c1", "content": "Mémorisé (#1)."})
        self.assertGreater(self.db.task(tid)["cost_usd"], 0)


class TestBrave(Base):
    def _run(self, script):
        from test_core import FakeLLM
        tid = self.db.add_task("Chercher", "recherche")
        llm = FakeLLM(script)
        run_task(self.cfg, self.db, llm, self.guard, self.db.task(tid))
        return llm

    def test_brave_preferred_and_billed(self):
        self.make({"provider": "anthropic"})
        os.environ["BRAVE_API_KEY"] = "bk-test"
        fake = {"web": {"results": [{"title": "Étude <strong>marché</strong>", "url": "https://exemple.fr/a",
                                     "description": "Les <strong>commerces</strong> manquent de temps.", "extra_snippets": ["Détail 1"]}]}}
        calls = []
        with unittest.mock.patch.object(core_tools, "brave_request", lambda p: calls.append(p) or fake):
            llm = self._run([[{"type": "tool_use", "id": "t1", "name": "brave_search", "input": {"query": "avis google commerces"}}],
                             [{"type": "tool_use", "id": "t2", "name": "complete_task", "input": {"result": "ok"}}]])
        names = [t["name"] for t in llm.calls[0]["tools"]]
        self.assertIn("brave_search", names)
        self.assertNotIn("web_search", names)            # Brave remplace l'outil serveur
        self.assertEqual(calls[0]["country"], "FR")
        self.assertEqual(calls[0]["search_lang"], "fr")
        out = llm.calls[1]["messages"][-1]["content"][0]["content"]
        self.assertIn("Étude marché", out)
        self.assertIn("https://exemple.fr/a", out)
        self.assertEqual(self.db.searches_today(), 1)
        self.assertAlmostEqual(self.db.one("SELECT usd FROM costs WHERE model='brave-search'")[0], 0.005)

    def test_daily_search_limit(self):
        self.make({"provider": "anthropic", "budget": {"web_searches_per_day": 1}})
        os.environ["BRAVE_API_KEY"] = "bk-test"
        self.db.add_cost("recherche", None, "brave-search", 0, 0, 1, 0.005)
        llm = self._run([[{"type": "tool_use", "id": "t2", "name": "complete_task", "input": {"result": "ok"}}]])
        self.assertNotIn("brave_search", [t["name"] for t in llm.calls[0]["tools"]])

    def test_no_search_tool_for_openai_without_brave(self):
        self.make({"provider": "openai"})
        llm = self._run([[{"type": "tool_use", "id": "t2", "name": "complete_task", "input": {"result": "ok"}}]])
        names = [t.get("name") for t in llm.calls[0]["tools"]]
        self.assertNotIn("web_search", names)
        self.assertNotIn("brave_search", names)
        self.assertIn("fetch_url", names)


if __name__ == "__main__":
    unittest.main()
