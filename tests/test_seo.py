"""Analyse SEO technique : vrai téléchargement contre un petit site servi en local (aucun accès à internet)."""
import functools
import http.server
import os
import threading
import unittest
import unittest.mock
from pathlib import Path

from test_core import Base

from ai_studio import seo
from ai_studio.tools import ToolContext, run_tool

SITE = Path(__file__).parent / "fixtures" / "site_seo"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ancien":                                # redirection interne
            self.send_response(301); self.send_header("Location", "/"); self.end_headers(); return
        if self.path == "/vers-prive":                            # redirection vers une adresse privée
            self.send_response(302); self.send_header("Location", "http://192.168.1.1/admin"); self.end_headers(); return
        super().do_GET()

    def log_message(self, *a):
        pass


class SeoTest(Base):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(SITE)))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        super().setUp()
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
        self.local = lambda h: h == "127.0.0.1"                      # seul le serveur de test est « public »
        from ai_studio import net                                     # … y compris pour la connexion à l'IP vérifiée
        real = net.ip_allowed
        p = unittest.mock.patch.object(net, "ip_allowed", lambda ip: ip == "127.0.0.1" or real(ip))
        p.start(); self.addCleanup(p.stop)

    def test_site_analysis_facts_and_issues(self):
        r = seo.check_site(self.url + "/ancien", 5, is_public=self.local)
        self.assertEqual(r["home_status"], 200)
        self.assertEqual(r["redirects"][0][0], 301)
        home = r["pages"][0]
        self.assertEqual(home["title"], "Plomberie Martin — plombier à Lyon 3e, dépannage 7j/7")
        self.assertEqual(home["h1"], ["Plombier à Lyon 3e"])
        self.assertEqual((home["images"], home["images_no_alt"], home["lang"]), (2, 1, "fr"))
        self.assertEqual(r["business"]["name"], "Plomberie Martin")              # type « Plumber » reconnu
        self.assertEqual(r["phones"], ["0472000000", "0612345678"])
        self.assertIn("contact@plomberie-martin.fr", r["emails"])
        self.assertTrue(r["robots_txt"]); self.assertEqual(r["sitemap_urls"], 2)
        msgs = [(i["level"], i["msg"]) for i in r["issues"]]
        self.assertIn(("critique", "Le site n'est pas servi en HTTPS"), msgs)
        self.assertTrue(any("Lien interne cassé (404)" in m for _, m in msgs))
        self.assertTrue(any("« Contact »" in m for _, m in msgs))
        self.assertFalse(any("noindex" in m for _, m in msgs))                    # normal sur les mentions légales
        self.assertFalse(any("Contenu très court" in m and "contact" in i["source"] for i in r["issues"] for m in [i["msg"]]))
        txt = seo.report(r)
        self.assertIn("Score technique :", txt); self.assertIn("LIMITES", txt)

    def test_private_redirect_refused(self):
        r = seo.fetch(self.url + "/vers-prive", is_public=self.local)
        self.assertEqual(r["status"], 0); self.assertIn("non publique", r["error"])
        r = seo.check_site("http://192.168.1.1/", 3, is_public=self.local)
        self.assertEqual(r["issues"][0]["level"], "critique")

    def test_robots_blocking_everything(self):
        pages = {"/": (200, {"content-type": "text/html"}, b"<html lang='fr'><head><title>Accueil du site de test assez long</title></head><body><h1>x</h1></body></html>"),
                 "/robots.txt": (200, {}, b"User-agent: *\nDisallow: /\n")}
        getter = lambda u: dict(zip(("status", "headers", "body"), pages.get("/" + u.split("/", 3)[3] if u.count("/") >= 3 else "/", (404, {}, b""))), ms=1)
        r = seo.check_site("https://exemple.fr/", 1, getter=getter, is_public=lambda h: True)
        self.assertTrue(any("bloque tout le site" in i["msg"] for i in r["issues"]))

    def test_tool_saves_and_reuses(self):
        from ai_studio.agents import ROLES
        tid = self.db.add_task("Audit", "atelier")
        ctx = ToolContext(self.cfg, self.db, "atelier", self.db.task(tid), list(ROLES))
        with unittest.mock.patch("ai_studio.tools.core._public_host", self.local):
            out = run_tool("seo_check", ctx, {"url": self.url})
            self.assertIn("Score technique", out)
            files = list((self.cfg.workspace / "analyses").glob("seo-127.0.0.1*.md"))
            self.assertEqual(len(files), 1)
            again = run_tool("seo_check", ctx, {"url": self.url + "/"})
        self.assertIn("reprise sans nouveau téléchargement", again)

    def test_method_injected_for_producer_only(self):
        from ai_studio import commerce
        from ai_studio.runner import _briefing
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe-lumiere.fr")
        prod = self.db.add_task("Produire le diagnostic", "atelier", order_ref=n)
        pm = self.db.one("SELECT id FROM tasks WHERE order_ref=? AND agent='chef_projet'", (n,))[0]
        self.assertIn("MÉTHODE DE PRODUCTION", _briefing(self.db, self.db.task(prod), self.cfg))
        self.assertIn("Score de présence locale", _briefing(self.db, self.db.task(prod), self.cfg))
        self.assertNotIn("MÉTHODE DE PRODUCTION", _briefing(self.db, self.db.task(pm), self.cfg))


if __name__ == "__main__":
    unittest.main()
