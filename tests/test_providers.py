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
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BRAVE_API_KEY", "OPENAI_BASE_URL", "STRIPE_SECRET_KEY"):
            os.environ.pop(k, None)
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def make(self, cfg: dict | None = None):
        (self.dir / "config.json").write_text(json.dumps(cfg or {}))
        self.cfg = load_config(self.dir)
        self.cfg.data["security"]["daily_review"] = False   # revue quotidienne testée à part
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

    def test_search_library_reuses_results(self):
        from ai_studio.tools import ToolContext, run_tool
        from ai_studio.db import iso, now
        from datetime import timedelta
        self.make({"provider": "anthropic"})
        os.environ["BRAVE_API_KEY"] = "bk-test"
        fake = {"web": {"results": [{"title": "Boulangeries à Lyon", "url": "https://exemple.fr/b", "description": "Annuaire des boulangeries"}]}}
        calls = []
        ctx = ToolContext(self.cfg, self.db, "recherche", self.db.task(self.db.add_task("t", "recherche")), [])
        with unittest.mock.patch.object(core_tools, "brave_request", lambda p: calls.append(p) or fake):
            first = run_tool("brave_search", ctx, {"query": "Boulangeries  Lyon"})
            again = run_tool("brave_search", ctx, {"query": "boulangeries lyon"})          # même requête, autre casse
            other = run_tool("brave_search", ctx, {"query": "boulangeries lyon", "country": "BE"})
        self.assertEqual(len(calls), 2)                                    # la 2e est resservie par la bibliothèque
        self.assertIn("déjà faite", again); self.assertIn("https://exemple.fr/b", again)
        self.assertNotIn("déjà faite", other)
        self.assertEqual(self.db.searches_today(), 2)                     # ni coût ni quota pour la reprise
        lib = run_tool("search_library", ctx, {"query": "boulangeries lyon"})
        self.assertIn("Recherche « boulangeries lyon »", lib)
        self.assertIn("une recherche web est justifiée", run_tool("search_library", ctx, {"query": "plombier Nantes"}))
        # page lue : resservie pendant 3 jours, sans nouveau téléchargement
        class Resp:
            headers = {"content-type": "text/html"}
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return "<html><title>Boulangerie Martin</title><body>Pain au levain, ouvert le dimanche.</body></html>".encode()
        opened = []
        with unittest.mock.patch.object(core_tools, "_public_host", lambda h: True), \
             unittest.mock.patch.object(core_tools.urllib.request, "urlopen", lambda *a, **k: opened.append(1) or Resp()):
            run_tool("fetch_url", ctx, {"url": "https://martin.fr/#accueil"})
            page = run_tool("fetch_url", ctx, {"url": "https://martin.fr/"})
        self.assertEqual(len(opened), 1)
        self.assertIn("reprise de la bibliothèque", page); self.assertIn("levain", page)
        # au-delà de 7 jours la recherche est refaite ; au-delà de 30 jours elle est purgée
        self.db.run("UPDATE web_cache SET created_at=? WHERE kind='search'", (iso(now() - timedelta(days=8)),))
        with unittest.mock.patch.object(core_tools, "brave_request", lambda p: calls.append(p) or fake):
            run_tool("brave_search", ctx, {"query": "boulangeries lyon"})
        self.assertEqual(len(calls), 3)
        self.db.run("UPDATE web_cache SET created_at=? WHERE kind='page'", (iso(now() - timedelta(days=31)),))
        self.assertEqual(self.db.purge_cache(30), 1)

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


class TestOpenAIErrors(Base):
    def _http_error(self, code, message):
        import io, urllib.error
        body = json.dumps({"error": {"message": message, "param": "reasoning_effort"}}).encode()
        return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(body))

    def test_reasoning_effort_none_is_sent(self):
        sent = []
        ok = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}], "usage": {}}
        client = OpenAIClient(api_key="k", transport=lambda u, b, h, t: sent.append(b) or ok)
        client.complete("gpt-6.1-terra", "sys", [{"role": "user", "content": "x"}],
                        [{"name": "recall", "description": "", "input_schema": {"type": "object", "properties": {}}}])
        self.assertEqual(sent[0]["reasoning_effort"], "none")

    def test_reasoning_effort_dropped_when_unsupported(self):
        sent = []
        ok = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}], "usage": {}}
        def transport(u, b, h, t):
            sent.append(dict(b))
            if "reasoning_effort" in b:
                raise self._http_error(400, "Unsupported parameter: 'reasoning_effort' is not supported with this model.")
            return ok
        client = OpenAIClient(api_key="k", transport=transport)
        r = client.complete("gpt-4o-mini", "sys", [{"role": "user", "content": "x"}])
        self.assertEqual(r["content"][0]["text"], "ok")
        self.assertNotIn("reasoning_effort", sent[-1])
        self.assertIsNone(client.reasoning_effort)

    def test_fatal_error_stops_cycle_and_keeps_tasks(self):
        from ai_studio.orchestrator import Studio
        self.make()
        os.environ["OPENAI_API_KEY"] = "k"
        calls = []
        def transport(u, b, h, t):
            calls.append(1)
            raise self._http_error(400, "Function tools with reasoning_effort are not supported for gpt-6.1-terra.")
        st = Studio(self.cfg, self.db, OpenAIClient(api_key="k", transport=transport))
        self.db.add_task("A", "data"); self.db.add_task("B", "data")
        s = st.cycle()
        self.assertIn("API 400", s["error"])
        self.assertEqual(len(calls), 1)                       # pas de nouvel essai, pas de tâche suivante
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM tasks WHERE status='pending'")[0], 2)

    def test_transient_error_not_retried_in_same_cycle(self):
        from ai_studio.orchestrator import Studio
        from ai_studio.llm import LLMError
        self.make()
        class Boom:
            n = 0
            def complete(self, *a, **k):
                Boom.n += 1
                raise LLMError("API 529 : surchargé", status=529)
        st = Studio(self.cfg, self.db, Boom())
        self.db.add_task("A", "data")
        s = st.cycle()
        self.assertEqual(Boom.n, 1)
        self.assertEqual(self.db.task(1)["status"], "pending")
        st.cycle()
        self.assertEqual(self.db.task(1)["status"], "failed")  # 2e essai, au cycle suivant


class TestCli(Base):
    def test_relancer_and_archiver(self):
        from ai_studio.__main__ import main
        self.make()
        os.environ["AI_STUDIO_HOME"] = str(self.dir)
        import ai_studio.__main__ as m
        with unittest.mock.patch.object(m, "load_config", lambda: self.cfg):
            oid = self.db.add_objective("Objectif test")
            t1 = self.db.add_task("Revue", "pdg", objective_id=oid)
            t2 = self.db.add_task("Autre", "data")
            self.db.set_task(t2, status="failed", attempts=2)
            main(["relancer"])
            self.assertEqual(self.db.task(t2)["status"], "pending")
            self.assertEqual(self.db.task(t2)["attempts"], 0)
            main(["archiver", str(oid)])
            self.assertEqual(self.db.one("SELECT status FROM objectives WHERE id=?", (oid,))[0], "paused")
            self.assertEqual(self.db.task(t1)["status"], "blocked")


class TestLongDeliverables(Base):
    def _ctx(self):
        from ai_studio.tools import ToolContext
        self.make()
        tid = self.db.add_task("Rapport", "recherche")
        return ToolContext(self.cfg, self.db, "recherche", self.db.task(tid), ["recherche"])

    def test_write_file_append(self):
        from ai_studio.tools import run_tool
        ctx = self._ctx()
        run_tool("write_file", ctx, {"path": "r/rapport.md", "content": "# Partie 1\n"})
        out = run_tool("write_file", ctx, {"path": "r/rapport.md", "content": "## Partie 2\n", "append": True})
        self.assertIn("complété", out)
        self.assertEqual((self.cfg.workspace / "r" / "rapport.md").read_text(encoding="utf-8"), "# Partie 1\n## Partie 2\n")

    def test_truncated_arguments_give_clear_advice(self):
        from ai_studio.tools import run_tool
        ctx = self._ctx()
        args = from_openai_response({"choices": [{"finish_reason": "length", "message": {"tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "a.md", "content": "texte coup'}}]}}]})["content"][0]["input"]
        out = run_tool("write_file", ctx, args)
        self.assertIn("append=true", out)

    def test_output_limit_raised(self):
        self.make()
        self.assertGreaterEqual(self.cfg["runtime"]["max_output_tokens"], 6000)

    def test_stripe_tools_hidden_without_key(self):
        from test_core import FakeLLM
        self.make()
        tid = self.db.add_task("Finances", "finance")
        llm = FakeLLM([[{"type": "tool_use", "id": "t", "name": "complete_task", "input": {"result": "ok"}}]])
        run_task(self.cfg, self.db, llm, self.guard, self.db.task(tid))
        names = [t.get("name") for t in llm.calls[0]["tools"]]
        self.assertNotIn("stripe_balance", names)
        self.assertNotIn("propose_payment_link", names)
        os.environ["STRIPE_SECRET_KEY"] = "rk_test"
        tid = self.db.add_task("Finances 2", "finance")
        llm = FakeLLM([[{"type": "tool_use", "id": "t", "name": "complete_task", "input": {"result": "ok"}}]])
        run_task(self.cfg, self.db, llm, self.guard, self.db.task(tid))
        self.assertIn("stripe_balance", [t.get("name") for t in llm.calls[0]["tools"]])


class TestHistoryCompaction(Base):
    def test_old_tool_results_are_shortened(self):
        from ai_studio.runner import compact_history, _clean_for_api
        big = "x" * 5000
        msgs = [{"role": "user", "content": "briefing"}]
        for i in range(4):
            msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "fetch_url", "input": {}}]})
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": big}]})
        saved = compact_history(msgs, keep_last=2, max_chars=600)
        self.assertGreater(saved, 8000)
        self.assertLess(len(msgs[2]["content"][0]["content"]), 600)
        self.assertEqual(len(msgs[8]["content"][0]["content"]), 5000)        # récents intacts
        self.assertEqual(compact_history(msgs, 2, 600), 0)                    # idempotent
        clean = _clean_for_api(msgs)
        self.assertNotIn("_compacte", clean[2]["content"][0])
        self.assertIn("_compacte", msgs[2]["content"][0])                     # l'original garde le marqueur

    def test_runner_sends_compacted_history(self):
        from test_core import FakeLLM
        self.make()
        tid = self.db.add_task("Lire", "data")
        long = "y" * 4000
        script = [[{"type": "tool_use", "id": f"r{i}", "name": "recall", "input": {"query": "q"}}] for i in range(4)]
        script.append([{"type": "tool_use", "id": "end", "name": "complete_task", "input": {"result": "ok"}}])
        llm = FakeLLM(script)
        from ai_studio.tools import REGISTRY
        orig = REGISTRY["recall"].func
        REGISTRY["recall"].func = lambda ctx, query: long
        try:
            run_task(self.cfg, self.db, llm, self.guard, self.db.task(tid))
        finally:
            REGISTRY["recall"].func = orig
        last = llm.calls[-1]["messages"]
        sizes = [len(b["content"]) for m in last if isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result"]
        self.assertEqual(sizes[-1], 4000)
        self.assertLess(sizes[0], 600)
        self.assertTrue(all("_compacte" not in b for m in last if isinstance(m["content"], list) for b in m["content"]))

    def test_rules_and_search_quota(self):
        from ai_studio.agents import RULES
        self.make()
        self.assertIn("coût d'API", RULES)
        self.assertEqual(self.cfg["budget"]["web_searches_per_day"], 30)


if __name__ == "__main__":
    unittest.main()
