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


class ServerBase(unittest.TestCase):
    """Serveur réel et fonctions d'appel (sans test : les classes de tests en héritent sans dupliquer)."""
    def setUp(self):
        env = unittest.mock.patch.dict(os.environ, {}, clear=False)
        env.start(); self.addCleanup(env.stop)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BRAVE_API_KEY", "OPENAI_BASE_URL", "STRIPE_SECRET_KEY"):
            os.environ.pop(k, None)
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.cfg = load_config(self.dir)
        self.cfg.data["security"]["daily_review"] = False   # revue quotidienne testée à part
        self.cfg.data["finance"] = {"daily_review": False}
        self.db = DB(self.cfg.db_path)
        self.addCleanup(self.db.conn.close)
        self.srv = make_server(self.cfg, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, method, path, body=None, headers=None, host=None, session=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if session:                                   # cookie posé par le lien d'accès (audit SEC-04)
            from ai_studio.auth import COOKIE, get_key
            h["Cookie"] = f"{COOKIE}={get_key(self.cfg)}"
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


class ServerTest(ServerBase):

    def test_agent_rules_list_and_remove(self):
        rid = self.db.run("INSERT INTO agent_rules(agent, rule, created_at) VALUES ('ventes', 'Consulte list_prospects avant tout email.', '2026-11-28T10:00:00+00:00')")
        st, rules, _ = self.req("GET", "/api/agent-rules")
        self.assertEqual((st, rules[0]["agent"]), (200, "ventes"))
        st, r, _ = self.post(f"/api/agent-rules/{rid}/remove", {})
        self.assertEqual((st, r["ok"]), (200, True))
        self.assertEqual(self.req("GET", "/api/agent-rules")[1], [])
        self.assertEqual(self.post(f"/api/agent-rules/{rid}/remove", {})[0], 404)

    def test_library(self):
        self.db.cache_put("search", "FR|boulangeries lyon", "recherche", None, "1. Boulangeries à Lyon https://exemple.fr")
        self.db.cache_put("page", "https://martin.fr/", "atelier", None, "Pain au levain")
        st, lib, _ = self.req("GET", "/api/library")
        self.assertEqual((st, lib["total"], lib["searches"]), (200, 2, 1))
        self.assertEqual(lib["items"][1]["key"], "boulangeries lyon")
        st, lib, _ = self.req("GET", "/api/library?q=levain")
        self.assertEqual([i["kind"] for i in lib["items"]], ["page"])
        st, item, _ = self.req("GET", "/api/library?id=%d" % lib["items"][0]["id"])
        self.assertEqual(item["content"], "Pain au levain")

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
        self.assertEqual(len(ag), 13)
        self.assertEqual(by["atelier"]["division"], "Division production")
        self.assertEqual(by["data"]["division"], "Tech & Données")
        self.assertEqual(by["securite"]["division"], "Tech & Données")
        self.assertEqual(by["finance"]["division"], "Direction")
        self.assertEqual(by["recherche"]["done"], 1)
        self.assertEqual((by["recherche"]["level"], by["ventes"]["level"]), (1, 1))   # 10 tâches réussies par niveau
        for i in range(10):
            self.db.set_task(self.db.add_task(f"T{i}", "recherche"), status="done", result="ok")
        st, ag, _ = self.req("GET", "/api/agents")
        self.assertEqual({a["name"]: a for a in ag}["recherche"]["level"], 2)
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


    def test_zip_and_html_preview(self):
        import zipfile
        from test_quality import GOOD, site
        d = self.cfg.workspace / "livraisons" / "commande-1" / "site"
        site(d, {"css/style.css": "body{color:red}", "app.txt": "x"})
        with zipfile.ZipFile(d.parent / "site.zip", "w") as z:
            for f in d.rglob("*"):
                z.write(f, "site/" + f.relative_to(d).as_posix())
        (d.parent / "rapport.html").write_text(GOOD, encoding="utf-8")
        a1 = self.db.add_approval("support", None, "send_email", {"to": "a@b.fr", "subject": "s", "body": "b", "attachment": "livraisons/commande-1/site.zip"}, "r")
        a2 = self.db.add_approval("support", None, "send_email", {"to": "a@b.fr", "subject": "s", "body": "b", "attachment": "livraisons/commande-1/rapport.html"}, "r")
        st, body, r = self.req("GET", f"/api/approvals/{a1}/livrable/index.html")
        self.assertEqual(st, 200); self.assertIn("Boulangerie Martin", body)
        self.assertIn("sandbox", r.getheader("Content-Security-Policy"))
        st, body, r = self.req("GET", f"/api/approvals/{a1}/livrable/css/style.css")
        self.assertEqual((st, r.getheader("Content-Type")), (200, "text/css; charset=utf-8"))
        self.assertEqual(self.req("GET", f"/api/approvals/{a1}/livrable/../../../secret.html")[0], 404)
        self.assertEqual(self.req("GET", f"/api/approvals/{a1}/livrable/absent.html")[0], 404)
        st, body, _ = self.req("GET", f"/api/approvals/{a2}/livrable")
        self.assertEqual(st, 200); self.assertIn("four à bois", body)


class MaquetteTest(ServerBase):
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
        # quartiers par pôle, un bâtiment par agent (3 au choix, 6 pour le Data Core), 2 terrains réservés
        from ai_studio.agents import ROLES
        st, meta, _ = self.req("GET", "/carte.json")
        self.assertEqual(st, 200)
        self.assertEqual(sorted(a for ags in meta["agents_of_pole"].values() for a in ags), sorted(ROLES))
        self.assertEqual(meta["agents_of_pole"]["hq"], ["pdg", "chef_projet"])                # les chefs de projet logent au HQ
        for p in meta["poles"]:
            letters = "ABCDEF" if p["id"] == "data" else "ABC"
            self.assertEqual("".join(v["L"] for v in p["variants"]), letters)
            for v in letters:
                self.assertIn(f'class="bld" data-pole="{p["id"]}" data-variant="{v}"', svg)
            self.assertIn(f'class="office" data-pole="{p["id"]}"', svg)
        q = {x["id"]: x for x in meta["quarters"]}
        self.assertEqual(q["commercial"]["poles"], ["marketing", "sales", "support", "ecommerce"])
        self.assertEqual(sorted(q["tech"]["poles"]), ["auto", "data", "infra", "security"])
        self.assertEqual(sorted(q["direction"]["poles"]), ["finance", "hq", "legal", "rh"])
        # plateau « ville » : enseigne à l'avant de chaque îlot, pas de nom de code (rester professionnel)
        for p in meta["poles"]:
            self.assertNotIn("code", p)
            self.assertIn(">%s</text>" % p["name"].upper(), svg)
        # case centrale : place de rassemblement avec un personnage par agent (affiché quand il est au repos)
        from ai_studio.agents import ROLES as R2
        for name in R2:
            self.assertIn('class="idle" data-agent="%s"' % name, svg)
        # chaque pôle occupe un coin de 4 emplacements
        slots = {k: len(q[k]["poles"]) + q[k]["free"] for k in ("direction", "commercial", "production", "tech")}
        planned = {p["id"] for p in meta["poles"] if p["planned"]}
        self.assertEqual(planned, {"video", "ecommerce", "infra", "auto"})               # bâtiments des divisions en préparation (RH : auditeur)
        self.assertEqual((meta["agents_of_pole"]["video"], "video" in meta["agent_of_pole"]), ([], False))
        self.assertEqual(set(slots.values()), {4})

    def test_static_files_revalidated_not_stale(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/carte.svg", headers={"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse(); r.read()
        etag = r.getheader("ETag")
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")     # jamais d'ancienne carte après une mise à jour
        self.assertTrue(etag)
        c.request("GET", "/carte.svg", headers={"Host": f"127.0.0.1:{self.port}", "If-None-Match": etag})
        r = c.getresponse(); body = r.read(); c.close()
        self.assertEqual((r.status, body), (304, b""))

    def test_activities_api(self):
        st, _, _ = self.post("/api/objectives", {"text": "Lancer la boutique", "activity": "ecommerce"})
        self.assertEqual(st, 200)
        st, _, _ = self.post("/api/objectives", {"text": "Objectif inconnu", "activity": "lune"})
        self.assertEqual(st, 400)
        _, objs, _ = self.req("GET", "/api/objectives")
        self.assertEqual(objs[0]["activity"], "ecommerce")
        _, acts, _ = self.req("GET", "/api/activities")
        self.assertEqual([a["id"] for a in acts], ["services", "video", "ecommerce", None])

    def test_page_follows_mockup_sections(self):
        _, html, _ = self.req("GET", "/")
        for s in ("Carte de l’entreprise", "Vue bureaux", "Bâtiment du pôle", "Performance", "Fil d’activité", "Succès",
                  "Escouade d’agents", "File de tâches", "Pipeline de revenus", "Prochains événements", "Niveau du HQ", "Progression globale"):
            self.assertIn(s, html)
        self.assertNotIn("__ICONS__", html)

    def test_map_settings_persist(self):
        _, m, _ = self.req("GET", "/api/map")
        self.assertEqual(m["view"], "ville")
        st, m, _ = self.post("/api/map", {"pole": "security", "variant": "C"})
        self.assertEqual(st, 200)
        self.post("/api/map", {"view": "bureaux"})
        _, m, _ = self.req("GET", "/api/map")
        self.assertEqual(m, {"variants": {"security": "C"}, "view": "bureaux"})
        st, m, _ = self.post("/api/map", {"pole": "data", "variant": "F"})
        self.assertEqual((st, m["variants"]["data"]), (200, "F"))
        st, _, _ = self.post("/api/map", {"pole": "commercial", "variant": "A"})    # un quartier n'a pas de bâtiment propre
        self.assertEqual(st, 400)
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


class AccessTest(ServerBase):
    """Accès protégé par clé secrète et cookie de session (audit SEC-04)."""

    def test_local_program_without_key_cannot_read_or_act(self):
        aid = self.db.add_approval("ventes", None, "decision", {"question": "On y va ?"}, "r")
        st, body, _ = self.req("GET", "/api/approvals", session=False)
        self.assertEqual(st, 401); self.assertIn("lien", body["error"])
        st, _, _ = self.req("POST", f"/api/approvals/{aid}", {"decision": "approve"}, {"X-AI-Studio": "1"}, session=False)
        self.assertEqual(st, 401)                                    # l'en-tête seul ne suffit plus
        self.assertEqual(self.db.approval(aid)["status"], "pending")

    def test_dashboard_page_requires_session(self):
        st, body, _ = self.req("GET", "/", session=False)
        self.assertEqual(st, 401)
        self.assertIn("Accès protégé", body); self.assertNotIn("renderApprovals", body)
        st, body, _ = self.req("GET", "/")
        self.assertEqual(st, 200); self.assertIn("renderApprovals", body)

    def test_access_link_sets_cookie_and_hides_key(self):
        from ai_studio.auth import COOKIE, get_key
        st, _, r = self.req("GET", "/?cle=mauvaise-cle", session=False)
        self.assertEqual(st, 403)
        st, _, r = self.req("GET", f"/?cle={get_key(self.cfg)}", session=False)
        self.assertEqual((st, r.getheader("Location")), (303, "/"))           # la clé quitte la barre d'adresse
        cookie = r.getheader("Set-Cookie")
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, cookie)
        value = cookie.split(";")[0]
        st, _, _ = self.req("GET", "/api/status", headers={"Cookie": value}, session=False)
        self.assertEqual(st, 200)
        st, _, _ = self.req("GET", "/api/status", headers={"Cookie": f"{COOKIE}=devine"}, session=False)
        self.assertEqual(st, 401)

    def test_cross_site_post_refused_even_with_cookie(self):
        aid = self.db.add_approval("ventes", None, "decision", {"question": "On y va ?"}, "r")
        body = {"decision": "approve", "note": "oui"}
        self.assertEqual(self.req("POST", f"/api/approvals/{aid}", body, {"X-AI-Studio": "1", "Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.req("POST", f"/api/approvals/{aid}", body, {"X-AI-Studio": "1", "Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.req("POST", f"/api/approvals/{aid}", body)[0], 403)        # sans l'en-tête de la page
        self.assertEqual(self.db.approval(aid)["status"], "pending")
        st, _, _ = self.req("POST", f"/api/approvals/{aid}", body,
                            {"X-AI-Studio": "1", "Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(st, 200)

    def test_key_is_stable_private_and_out_of_agents_reach(self):
        from ai_studio import auth
        from ai_studio.agents import ROLES
        from ai_studio.tools import ToolContext, run_tool
        k = auth.get_key(self.cfg)
        self.assertEqual(auth.get_key(self.cfg), k)                     # même clé d'un démarrage à l'autre
        self.assertGreaterEqual(len(k), 40)
        p = auth.key_path(self.cfg)
        self.assertNotIn(self.cfg.workspace.resolve(), p.resolve().parents)
        if os.name == "posix":
            self.assertEqual(p.stat().st_mode & 0o077, 0)              # lisible par le seul compte utilisateur
        tid = self.db.add_task("t", "securite")
        ctx = ToolContext(self.cfg, self.db, "securite", self.db.task(tid), list(ROLES))
        self.assertIn("hors de l'espace de travail", run_tool("read_file", ctx, {"path": "../cle-tableau-de-bord.txt"}))
