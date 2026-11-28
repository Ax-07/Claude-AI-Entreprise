"""Client réseau sécurisé (audit SEC-02) : vrai serveur HTTP local + DNS simulé, aucun accès à Internet.

Le serveur de test écoute sur 127.0.0.1 ; pour ces tests seulement, cette adresse est traitée comme « publique »
(nom site-public.test). Tout le reste du réseau local reste interdit.
"""
import functools
import http.server
import os
import socket
import threading
import unittest
import unittest.mock

from ai_studio import net
from ai_studio.agents import ROLES
from ai_studio.tools import ToolContext, run_tool
from test_core import Base

REAL_GETADDRINFO = socket.getaddrinfo
REAL_ALLOWED = net.ip_allowed


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def do_GET(self):
        _Handler.hits.append((self.headers.get("Host"), self.path))
        port = self.server.server_address[1]
        redirects = {"/vers-box": f"http://box.test:{port}/admin",
                     "/vers-meta": "http://169.254.169.254/latest/meta-data/",
                     "/vers-ipv6": f"http://[::ffff:10.0.0.1]:{port}/",
                     "/vers-page": "/page", "/boucle": "/boucle"}
        if self.path in redirects:
            self.send_response(302); self.send_header("Location", redirects[self.path]); self.end_headers(); return
        body = "<html><title>Page publique</title><body>Contenu de la page publique de test.</body></html>".encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


def dns(table):
    """getaddrinfo simulé : les noms de la table, le reste comme d'habitude."""
    def fake(host, port, *a, **k):
        if host in table:
            ips = table[host]() if callable(table[host]) else table[host]
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
        return REAL_GETADDRINFO(host, port, *a, **k)
    return fake


class NetTest(Base):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        super().setUp()
        _Handler.hits = []
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
        p = unittest.mock.patch.object(net, "ip_allowed", lambda ip: ip == "127.0.0.1" or REAL_ALLOWED(ip))
        p.start(); self.addCleanup(p.stop)
        from ai_studio.tools import core as core_tools                  # contrôle de départ : même règle d'adresse
        p = unittest.mock.patch.object(core_tools, "_public_host", lambda h: net.is_public_host(h))
        p.start(); self.addCleanup(p.stop)
        self.table = {"site-public.test": ["127.0.0.1"], "box.test": ["192.168.1.1"]}
        p = unittest.mock.patch("socket.getaddrinfo", dns(self.table))
        p.start(); self.addCleanup(p.stop)
        tid = self.db.add_task("Veille", "recherche")
        self.ctx = ToolContext(self.cfg, self.db, "recherche", self.db.task(tid), list(ROLES))

    def url(self, path):
        return f"http://site-public.test:{self.port}{path}"

    def fetch(self, path):
        return run_tool("fetch_url", self.ctx, {"url": self.url(path)})

    # ------------------------------------------------------------ règles d'adresse
    def test_only_internet_addresses_are_allowed(self):
        refused = ["127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
                   "224.0.0.1", "240.0.0.1", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1", "::ffff:192.168.0.1",
                   "2002:7f00:1::", "2002:c0a8:101::", "pas-une-ip"]
        for ip in refused:
            self.assertFalse(REAL_ALLOWED(ip), ip)
        for ip in ("8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"):
            self.assertTrue(REAL_ALLOWED(ip), ip)

    def test_mixed_dns_answer_is_refused(self):
        self.table["mixte.test"] = ["93.184.216.34", "10.0.0.7"]               # une seule adresse privée suffit
        self.assertFalse(net.is_public_host("mixte.test"))
        self.assertIn("non publique", run_tool("fetch_url", self.ctx, {"url": "http://mixte.test/"}))

    def test_urls_with_credentials_or_other_schemes_refused(self):
        for u in ("file:///etc/passwd", f"http://moi:secret@site-public.test:{self.port}/", "ftp://site-public.test/"):
            with self.assertRaises(net.NetError):
                net.safe_get(u)

    # ------------------------------------------------------------ redirections
    def test_public_page_works_and_internal_redirect_followed(self):
        out = self.fetch("/vers-page")
        self.assertIn("Contenu de la page publique", out)
        self.assertEqual([p for _, p in _Handler.hits], ["/vers-page", "/page"])

    def test_redirect_to_local_network_is_refused(self):
        out = self.fetch("/vers-box")
        self.assertIn("Refusé", out); self.assertIn("non publique", out)
        self.assertNotIn("/admin", [p for _, p in _Handler.hits])             # la cible n'a jamais été contactée

    def test_redirect_to_cloud_metadata_and_mapped_ipv6_refused(self):
        self.assertIn("non publique", self.fetch("/vers-meta"))
        self.assertIn("non publique", self.fetch("/vers-ipv6"))

    def test_redirect_loop_stops(self):
        self.assertIn("trop de redirections", self.fetch("/boucle"))
        self.assertEqual(len(_Handler.hits), net.MAX_REDIRECTS + 1)

    # ------------------------------------------------------------ DNS rebinding
    def test_connection_goes_to_the_checked_ip(self):
        answers = iter([["127.0.0.1"]])                                    # 1re réponse publique (contrôle)…
        self.table["rebind.test"] = lambda: next(answers, ["10.0.0.5"])   # … puis une adresse locale
        connected = []
        real = socket.create_connection
        def spy(addr, *a, **k):
            connected.append(addr[0])
            return real(addr, *a, **k)
        with unittest.mock.patch("socket.create_connection", spy):
            out = run_tool("fetch_url", self.ctx, {"url": f"http://rebind.test:{self.port}/page"})
        self.assertIn("Contenu de la page publique", out)
        self.assertEqual(connected, ["127.0.0.1"])                         # jamais 10.0.0.5
        self.assertEqual(_Handler.hits[-1][0], f"rebind.test:{self.port}")  # le nom reste dans l'en-tête Host

    def test_seo_fetch_uses_pinned_client(self):
        from ai_studio import seo
        r = seo.fetch(self.url("/vers-box"), is_public=net.is_public_host)
        self.assertEqual(r["status"], 0); self.assertIn("non publique", r["error"])
        answers = iter([["127.0.0.1"], ["127.0.0.1"]])                     # contrôle de seo.fetch + résolution épinglée
        self.table["rebind.test"] = lambda: next(answers, ["10.0.0.5"])
        r = seo.fetch(f"http://rebind.test:{self.port}/page", is_public=net.is_public_host)
        self.assertEqual(r["status"], 200)


if __name__ == "__main__":
    unittest.main()
