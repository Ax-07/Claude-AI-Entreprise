"""Tests de l'API locale et du tableau de bord (serveur réel sur un port aléatoire, sans appel aux IA)."""
import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

from ai_studio.config import load_config
from ai_studio.db import DB
from ai_studio.server import make_server


class ServerTest(unittest.TestCase):
    def setUp(self):
        env = unittest.mock.patch.dict(os.environ, {}, clear=False)
        env.start(); self.addCleanup(env.stop)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BRAVE_API_KEY", "OPENAI_BASE_URL", "STRIPE_SECRET_KEY"):
            os.environ.pop(k, None)
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.cfg = load_config(self.dir)
        self.db = DB(self.cfg.db_path)
        self.addCleanup(self.db.conn.close)
        self.srv = make_server(self.cfg, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        ctype = r.getheader("Content-Type") or ""
        return r.status, (json.loads(raw) if "json" in ctype else raw.decode("utf-8")), r

    def post(self, path, body):
        return self.req("POST", path, body, {"X-AI-Studio": "1"})

    def test_dashboard_page_is_served_with_security_headers(self):
        st, html, r = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("Command HQ", html)
        self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")

    def test_status_and_agents_reflect_database(self):
        oid = self.db.add_objective("Objectif test")
        t = self.db.add_task("Étude", "recherche", objective_id=oid)
        self.db.set_task(t, status="done", result="ok")
        self.db.add_task("Suite", "ventes")
        self.db.add_cost("recherche", t, "gpt-6-luna", 1000, 100, 1, 0.01)
        st, s, _ = self.req("GET", "/api/status")
        self.assertEqual(st, 200)
        self.assertEqual(s["tasks"]["done"], 1)
        self.assertEqual(s["tasks"]["pending"], 1)
        self.assertEqual(s["xp"], 10)
        self.assertAlmostEqual(s["budget"]["day"], 0.01)
        self.assertEqual(s["objective"]["tasks_done"], 1)
        self.assertFalse(s["engine"]["running"])
        st, ag, _ = self.req("GET", "/api/agents")
        by = {a["name"]: a for a in ag}
        self.assertEqual(len(ag), 8)
        self.assertEqual(by["recherche"]["done"], 1)
        self.assertEqual(by["ventes"]["state"], "waiting")

    def test_engine_heartbeat(self):
        from ai_studio.db import iso
        self.db.kv_set("last_cycle_at", iso())
        _, s, _ = self.req("GET", "/api/status")
        self.assertTrue(s["engine"]["running"])

    def test_post_requires_custom_header(self):
        st, body, _ = self.req("POST", "/api/objectives", {"text": "Objectif pirate"})
        self.assertEqual(st, 403)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM objectives")[0], 0)

    def test_foreign_host_rejected(self):
        st, _, _ = self.req("GET", "/api/status", host="evil.example.com")
        self.assertEqual(st, 403)

    def test_approve_from_dashboard(self):
        tid = self.db.add_task("Question", "pdg")
        aid = self.db.add_approval("pdg", tid, "decision", {"question": "On lance ?"}, "On lance ?")
        _, lst, _ = self.req("GET", "/api/approvals")
        self.assertEqual(lst[0]["payload"]["question"], "On lance ?")
        st, r, _ = self.post(f"/api/approvals/{aid}", {"decision": "approve", "note": "Oui"})
        self.assertEqual(st, 200)
        self.assertIn("Oui", r["message"])
        self.assertEqual(self.db.approval(aid)["status"], "approved")
        follow = self.db.q("SELECT * FROM tasks WHERE created_by='proprietaire'")
        self.assertEqual(follow[0]["agent"], "pdg")
        st, _, _ = self.post(f"/api/approvals/{aid}", {"decision": "maybe"})
        self.assertEqual(st, 400)

    def test_objectives_and_retry(self):
        st, r, _ = self.post("/api/objectives", {"text": "Concevoir une offre"})
        self.assertEqual(st, 200)
        oid = r["id"]
        tid = self.db.add_task("Revue", "pdg", objective_id=oid)
        st, _, _ = self.post(f"/api/objectives/{oid}", {"status": "paused"})
        self.assertEqual(self.db.task(tid)["status"], "blocked")
        st, _, _ = self.post(f"/api/tasks/{tid}/retry", {})
        self.assertEqual(st, 200)
        self.assertEqual(self.db.task(tid)["status"], "pending")
        st, _, _ = self.post(f"/api/tasks/{tid}/retry", {})
        self.assertEqual(st, 400)
        _, objs, _ = self.req("GET", "/api/objectives")
        self.assertEqual(objs[0]["status"], "paused")

    def test_task_detail_events_and_filters(self):
        tid = self.db.add_task("Étude", "recherche", "consigne")
        self.db.log("recherche", "tool", "brave_search(...)", tid)
        child = self.db.add_task("Sous-tâche", "ventes", parent_id=tid)
        _, d, _ = self.req("GET", f"/api/tasks/{tid}")
        self.assertEqual(d["description"], "consigne")
        self.assertEqual(d["children"][0]["id"], child)
        self.assertEqual(len(d["events"]), 1)
        _, lst, _ = self.req("GET", "/api/tasks?agent=ventes")
        self.assertEqual([t["id"] for t in lst], [child])
        st, _, _ = self.req("GET", "/api/tasks/999")
        self.assertEqual(st, 404)
        _, ev, _ = self.req("GET", "/api/events?since=0")
        self.assertEqual(ev[0]["message"], "brave_search(...)")

    def test_files_are_sandboxed(self):
        (self.cfg.workspace / "recherche").mkdir(parents=True, exist_ok=True)
        (self.cfg.workspace / "recherche" / "rapport.md").write_text("# Rapport\nété", encoding="utf-8")
        _, fl, _ = self.req("GET", "/api/files")
        self.assertEqual(fl[0]["path"], "recherche/rapport.md")
        _, f, _ = self.req("GET", "/api/file?path=recherche/rapport.md")
        self.assertIn("été", f["content"])
        st, _, _ = self.req("GET", "/api/file?path=../../config.json")
        self.assertEqual(st, 400)
        st, _, _ = self.req("GET", "/../ai_studio/server.py")
        self.assertEqual(st, 404)

    def test_costs(self):
        self.db.add_cost("pdg", None, "gpt-6.1-terra", 1000, 100, 0, 0.02)
        _, c, _ = self.req("GET", "/api/costs")
        self.assertEqual(c["by_agent"][0]["agent"], "pdg")
        self.assertEqual(len(c["days"]), 1)


class MaquetteTest(ServerTest):
    def test_map_assets_served_compressed(self):
        import gzip as gz
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/carte.svg", headers={"Host": f"127.0.0.1:{self.port}", "Accept-Encoding": "gzip"})
        r = c.getresponse(); raw = r.read(); c.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Encoding"), "gzip")
        svg = gz.decompress(raw).decode("utf-8")
        self.assertLess(len(raw), len(svg) / 3)
        self.assertNotIn("{{", svg)
        for pole in ("hq", "research", "marketing", "sales", "support", "data", "finance", "atelier"):
            for v in "ABC":
                self.assertIn(f'class="bld" data-pole="{pole}" data-variant="{v}"', svg)
            self.assertIn(f'class="office" data-pole="{pole}"', svg)
        st, meta, _ = self.req("GET", "/carte.json")
        self.assertEqual(st, 200)
        self.assertEqual(meta["agent_of_pole"]["sales"], "ventes")
        self.assertEqual(len(meta["poles"]), 8)
        self.assertEqual(meta["agent_of_pole"]["atelier"], "atelier")

    def test_page_follows_mockup_sections(self):
        _, html, _ = self.req("GET", "/")
        for s in ("Carte de l’entreprise", "Vue bureaux", "Bâtiment du pôle", "Performance", "Fil d’activité", "Succès",
                  "Escouade d’agents", "File de tâches", "Pipeline de revenus", "Prochains événements", "Niveau du HQ", "Progression globale"):
            self.assertIn(s, html)
        self.assertNotIn("__ICONS__", html)

    def test_map_settings_persist(self):
        _, m, _ = self.req("GET", "/api/map")
        self.assertEqual(m["view"], "ville")
        st, m, _ = self.post("/api/map", {"pole": "support", "variant": "C"})
        self.assertEqual(st, 200)
        self.post("/api/map", {"view": "bureaux"})
        _, m, _ = self.req("GET", "/api/map")
        self.assertEqual(m, {"variants": {"support": "C"}, "view": "bureaux"})
        st, _, _ = self.post("/api/map", {"pole": "support", "variant": "Z"})
        self.assertEqual(st, 400)

    def test_insights_are_real(self):
        from ai_studio.db import iso
        t = self.db.add_task("A", "data"); self.db.set_task(t, status="done")
        t2 = self.db.add_task("B", "data"); self.db.set_task(t2, status="failed")
        self.db.add_cost("data", t, "gpt-6-luna", 10, 10, 0, 0.02)
        self.db.add_objective("Objectif")
        self.db.add_approval("ventes", None, "send_email", {"to": "a@b.fr", "subject": "s", "body": "b"}, "r")
        _, d, _ = self.req("GET", "/api/insights?days=7")
        self.assertEqual(len(d["days"]), 7)
        self.assertEqual(d["done"][-1], 1)
        self.assertEqual(d["success_rate"][-1], 50)
        self.assertAlmostEqual(d["cost"][-1], 0.02)
        self.assertEqual({p["stage"]: p["value"] for p in d["pipeline"]}["Emails préparés"], 1)
        self.assertTrue(any("Revue de l'objectif" in s["title"] for s in d["schedule"]))
        _, d30, _ = self.req("GET", "/api/insights?days=30")
        self.assertEqual(len(d30["days"]), 30)

    def test_integrations(self):
        os.environ["BRAVE_API_KEY"] = "x"
        _, lst, _ = self.req("GET", "/api/integrations")
        by = {i["name"]: i["ok"] for i in lst}
        self.assertTrue(by["Brave Search"])
        self.assertFalse(by["Stripe"])


if __name__ == "__main__":
    unittest.main()
