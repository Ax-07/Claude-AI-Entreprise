"""Conversion PDF isolée (audit SEC-07) : HTML nettoyé, politique de sécurité injectée, navigateur sans réseau.

Les tests du nettoyage n'utilisent pas de navigateur. Le test de bout en bout utilise le vrai Edge ou Chrome s'il est
installé, et pdftotext pour lire le PDF produit (sinon il est ignoré).
"""
import os
import shutil
import subprocess
import unittest
import unittest.mock
from pathlib import Path

from ai_studio import pdf
from test_core import Base

BODY = "<p>" + "Constat vérifié et sourcé. " * 200 + "</p>"


class SanitizeTest(unittest.TestCase):
    def clean(self, html):
        return pdf.sanitize_html(html)

    def test_scripts_frames_and_handlers_removed(self):
        out = self.clean('<h1 onclick="x()">Titre</h1><script>alert(1)</script><iframe src="file:///etc/passwd">a</iframe>'
                         '<object data="x.swf"><param name="a"></object><embed src="x"><img src="a.png" onerror="steal()">'
                         '<svg><script>bad()</script><a xlink:href="javascript:bad()">x</a></svg><template><p>t</p></template>')
        for bad in ("<script", "alert", "<iframe", "/etc/passwd", "<object", "<embed", "onerror", "onclick", "steal",
                    "bad()", "javascript:", "<template"):
            self.assertNotIn(bad, out)
        self.assertIn("<h1>Titre</h1>", out)
        self.assertIn('<img src="a.png">', out)

    def test_only_links_inside_the_deliverable_folder(self):
        refused = ["file:///etc/hostname", "/etc/hostname", "../../secret.png", "images/../../x.png", "C:/Users/x.png",
                   "C:\\\\Users\\\\x.png", "..\\\\x.png", "%2e%2e/x.png", "%252e%252e/x.png", "//evil.example/x.png",
                   "http://evil.example/x.png", "data:text/html,<script>", "javascript:alert(1)", " JaVaScRiPt:x"]
        for u in refused:
            self.assertFalse(pdf.safe_url(u), u)
        for u in ("images/logo.png", "./style.css", "a/b/../c.png", "data:image/png;base64,AAAA", "#section", ""):
            self.assertTrue(pdf.safe_url(u), u)
        self.assertTrue(pdf.safe_url("https://client.fr/", link=True))       # lien cliquable dans le PDF : pas chargé
        self.assertFalse(pdf.safe_url("https://client.fr/", link=False))
        out = self.clean('<img src="/etc/x.png"><img src="ok.png"><a href="https://client.fr/">site</a>'
                         '<link rel="stylesheet" href="http://evil.example/s.css"><link rel="stylesheet" href="style.css">'
                         '<link rel="prefetch" href="x"><img srcset="a.png 1x, file:///b.png 2x">')
        self.assertNotIn("/etc/x.png", out); self.assertIn('src="ok.png"', out)
        self.assertIn('href="https://client.fr/"', out)
        self.assertNotIn("evil.example", out); self.assertIn('href="style.css"', out)
        self.assertNotIn("prefetch", out); self.assertNotIn("file:", out)

    def test_css_cannot_load_outside(self):
        out = self.clean('<style>@import url("http://evil.example/a.css"); body{background:url(file:///etc/x.png)} '
                         'h1{background:url("img/ok.png")}</style><p style="background:url(//evil.example/t.png)">x</p>')
        self.assertNotIn("evil.example", out); self.assertNotIn("file:", out); self.assertNotIn("@import", out)
        self.assertIn('url("img/ok.png")', out)

    def test_meta_refresh_base_and_csp_override_removed(self):
        out = self.clean('<head><base href="http://evil.example/"><meta http-equiv="refresh" content="0;url=http://x">'
                         '<meta http-equiv="Content-Security-Policy" content="default-src *"><meta charset="utf-8"></head>')
        self.assertNotIn("<base", out); self.assertNotIn("refresh", out); self.assertNotIn("default-src *", out)
        self.assertIn('<meta charset="utf-8">', out)

    def test_text_and_entities_preserved(self):
        out = self.clean("<!doctype html><p>Prix &amp; délais &#8364; <b>89 €</b></p>")
        self.assertEqual(out, "<!doctype html><p>Prix &amp; délais &#8364; <b>89 €</b></p>")


class IsolationTest(Base):
    def report(self, html):
        d = self.cfg.workspace / "livraisons" / "commande-1"
        d.mkdir(parents=True, exist_ok=True)
        src = d / "rapport.html"
        src.write_text(html, encoding="utf-8")
        return src

    def test_browser_launched_isolated_with_csp(self):
        src = self.report("<html><head><title>R</title></head><body><script>x()</script>" + BODY + "</body></html>")
        seen = {}
        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            from urllib.parse import urlparse
            from urllib.request import url2pathname
            seen["page"] = Path(url2pathname(urlparse(cmd[-1]).path)).read_text(encoding="utf-8")
            Path([c for c in cmd if c.startswith("--print-to-pdf=")][0].split("=", 1)[1]).write_bytes(b"%PDF-1.4\n" + b"x" * 2000)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with unittest.mock.patch.object(pdf, "find_browser", return_value="/usr/bin/chrome"), \
             unittest.mock.patch.object(pdf.subprocess, "run", fake_run), \
             unittest.mock.patch.object(pdf.os, "geteuid", return_value=1000, create=True):
            pdf.html_to_pdf(self.cfg, src, src.with_suffix(".pdf"))
        self.assertIn("--host-resolver-rules=MAP * ~NOTFOUND", seen["cmd"])
        self.assertNotIn("--no-sandbox", seen["cmd"])
        self.assertIn("Content-Security-Policy", seen["page"]); self.assertIn("script-src 'none'", seen["page"])
        self.assertNotIn("<script", seen["page"])
        self.assertLess(seen["page"].index("Content-Security-Policy"), seen["page"].index("<title>"))   # en tête de page

    def test_root_requires_explicit_opt_in(self):
        src = self.report("<html><head></head><body>" + BODY + "</body></html>")
        env = {k: v for k, v in os.environ.items() if k != "PDF_NO_SANDBOX"}
        with unittest.mock.patch.object(pdf, "find_browser", return_value="/usr/bin/chrome"), \
             unittest.mock.patch.object(pdf.os, "geteuid", return_value=0, create=True), \
             unittest.mock.patch.dict(os.environ, env, clear=True), \
             unittest.mock.patch.object(pdf.subprocess, "run", side_effect=AssertionError("navigateur lancé")):
            with self.assertRaises(pdf.PdfError) as e:
                pdf.html_to_pdf(self.cfg, src, src.with_suffix(".pdf"))
        self.assertIn("root", str(e.exception))
        self.assertFalse(any(p.name.startswith(".impression-") for p in src.parent.iterdir()))

    def test_real_browser_runs_no_script_and_reads_no_local_file(self):
        browser = pdf.find_browser(self.cfg)
        if not browser or not shutil.which("pdftotext"):
            self.skipTest("navigateur ou pdftotext absent")
        if hasattr(os, "geteuid") and os.geteuid() == 0 and os.environ.get("PDF_NO_SANDBOX") != "1":
            self.skipTest("lancé en root sans PDF_NO_SANDBOX=1")
        secret = self.cfg.data_dir / "secret-local.txt"
        secret.write_text("SECRET-DU-PROPRIETAIRE", encoding="utf-8")
        src = self.report("<html><head><title>R</title></head><body><h1 id='t'>ORIGINAL</h1>" + BODY +
                          f"<iframe src='{secret.as_uri()}'></iframe><object data='../../secret-local.txt'></object>"
                          "<img src='x.png' onerror=\"document.getElementById('t').textContent='PIRATE'\">"
                          "<script>document.getElementById('t').textContent='PIRATE'</script></body></html>")
        out = pdf.html_to_pdf(self.cfg, src, src.with_suffix(".pdf"))
        text = subprocess.run(["pdftotext", str(out), "-"], capture_output=True, text=True).stdout
        self.assertIn("ORIGINAL", text)
        self.assertNotIn("PIRATE", text)
        self.assertNotIn("SECRET-DU-PROPRIETAIRE", text)


if __name__ == "__main__":
    unittest.main()
