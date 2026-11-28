"""Contrôle qualité des livrables et aperçu avant validation (sans réseau ni modèle)."""
import os
import unittest
import zipfile

from ai_studio import quality
from ai_studio.tools import ToolContext, run_tool
from test_core import Base

GOOD = ('<html lang="fr"><head><title>Boulangerie Martin</title><meta name="description" content="Pains au levain à Lyon">'
        '<meta name="viewport" content="width=device-width"><style>body{font-family:sans-serif}</style></head><body><h1>Boulangerie Martin</h1>'
        + "<p>Pain au levain cuit chaque matin dans notre four à bois. </p>" * 8 +
        '<a href="contact.html">Contact</a> <a href="mentions-legales.html">Mentions légales</a></body></html>')
PAGE = ('<html lang="fr"><head><title>{t}</title><meta name="description" content="d"><meta name="viewport" content="w"></head>'
        '<body><h1>{t}</h1>' + "<p>Texte de la page avec assez de mots pour être une vraie page du site. </p>" * 4 +
        '<a href="index.html">Accueil</a> <a href="mailto:contact@martin.fr">Écrire</a></body></html>')


def site(folder, extra=None):
    folder.mkdir(parents=True, exist_ok=True)
    files = {"index.html": GOOD, "contact.html": PAGE.format(t="Contact"), "mentions-legales.html": PAGE.format(t="Mentions légales"), **(extra or {})}
    for k, v in files.items():
        (folder / k).parent.mkdir(parents=True, exist_ok=True)
        (folder / k).write_text(v, encoding="utf-8")


class QualityTest(Base):
    def ctx(self, agent="developpement"):
        from ai_studio.agents import ROLES
        return ToolContext(self.cfg, self.db, agent, self.db.task(self.db.add_task("t", agent)), list(ROLES))

    def test_site_checks(self):
        d = self.cfg.workspace / "livraisons" / "commande-1" / "site"
        site(d)
        r = quality.check_path(self.cfg.workspace, d)
        self.assertEqual((r["errors"], r["warnings"]), ([], []))
        (d / "contact.html").write_text(PAGE.format(t="Contact").replace("index.html", "accueil.html") + " Lorem ipsum", encoding="utf-8")
        out = run_tool("check_deliverable", self.ctx(), {"path": "livraisons/commande-1/site"})
        self.assertIn("lien cassé vers « accueil.html »", out)
        self.assertIn("lorem ipsum", out.lower())

    def test_document_checks(self):
        r = quality.check_document("rapport.html", "<html><head><link rel=\"stylesheet\" href=\"style.css\"></head><body>court [à compléter]</body></html>")
        self.assertTrue(any("feuille de style" in e for e in r["errors"]))
        self.assertTrue(any("à compléter" in w for w in r["warnings"]))
        self.assertTrue(any("aucune source" in w for w in r["warnings"]))

    def test_delivery_blocked_until_fixed(self):
        from ai_studio import commerce
        from pathlib import Path
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")
        n = commerce.test_order(self.cfg, self.db, "Site vitrine", "proprio@studio.fr")
        d = self.cfg.workspace / "livraisons" / f"commande-{n}" / "site"
        site(d, {"services.html": PAGE.format(t="Services").replace("index.html", "tarifs.html")})
        self.assertIn("Archive prête", run_tool("package_files", self.ctx(), {"folder": f"livraisons/commande-{n}/site", "name": "site"}))
        args = {"order": n, "attachment": f"livraisons/commande-{n}/site.zip", "subject": "Votre site", "body": "Bonjour"}
        out = run_tool("deliver_order", self.ctx("support"), args)
        self.assertIn("Livraison refusée", out); self.assertIn("tarifs.html", out)
        (d / "services.html").write_text(PAGE.format(t="Services"), encoding="utf-8")
        run_tool("package_files", self.ctx(), {"folder": f"livraisons/commande-{n}/site", "name": "site"})
        self.assertIn("en attente de validation", run_tool("deliver_order", self.ctx("support"), args))
        self.assertIn("Aucun problème", self.db.pending_approvals()[0]["payload"])


    def _report_order(self):
        from ai_studio import commerce
        from pathlib import Path
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "proprio@studio.fr")
        d = self.cfg.workspace / "livraisons" / f"commande-{n}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "rapport.html").write_text(
            "<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Diagnostic SEO local</title><style>body{font-family:sans-serif}</style></head><body>"
            "<h1>Diagnostic SEO local — ax-07.fr</h1>" + "<h2>Section</h2><p>Constat vérifiable, source : https://www.ax-07.fr/ consultée le 28/11/2026.</p>" * 30
            + "</body></html>", encoding="utf-8")
        return n, {"order": n, "attachment": f"livraisons/commande-{n}/rapport.html", "subject": "Votre diagnostic", "body": "Bonjour"}

    def test_html_report_delivered_as_pdf(self):
        import json as _j
        from unittest import mock
        from ai_studio import pdf
        n, args = self._report_order()
        def fake(cfg, src, dest, timeout=120):
            dest.write_bytes(b"%PDF-1.4\n" + b"/Type /Page\n" * 9 + b"x" * 2000)
            return dest
        with mock.patch.object(pdf, "html_to_pdf", fake):
            out = run_tool("deliver_order", self.ctx("support"), args)
        self.assertIn("converti en PDF", out)
        p = _j.loads(self.db.pending_approvals()[0]["payload"])
        self.assertEqual(p["attachment"], f"livraisons/commande-{n}/rapport.pdf")
        self.assertEqual((p["source"], p["pages"]), (f"livraisons/commande-{n}/rapport.html", 9))
        from ai_studio.server import deliverable_preview
        body, ctype = deliverable_preview(self.cfg, self.db, self.db.pending_approvals()[0]["id"])
        self.assertEqual(ctype, "application/pdf"); self.assertTrue(body.startswith(b"%PDF"))

    def test_pdf_failure_blocks_delivery(self):
        from unittest import mock
        from ai_studio import pdf
        n, args = self._report_order()
        with mock.patch.object(pdf, "find_browser", return_value=None):
            out = run_tool("deliver_order", self.ctx("support"), args)
        self.assertIn("Livraison impossible", out); self.assertIn("Edge", out)
        self.assertFalse(self.db.pending_approvals())                      # pas d'envoi du HTML à la place
        self.cfg.data["delivery"] = {"pdf": False}                          # option : livrer le HTML tel quel
        self.assertIn("en attente de validation", run_tool("deliver_order", self.ctx("support"), args))

    def test_real_pdf_conversion_when_browser_available(self):
        from ai_studio import pdf
        if not pdf.find_browser(self.cfg):
            self.skipTest("ni Edge ni Chrome sur cette machine")
        n, args = self._report_order()
        src = self.cfg.workspace / args["attachment"]
        out = pdf.html_to_pdf(self.cfg, src, src.with_suffix(".pdf"))
        self.assertTrue(out.read_bytes().startswith(b"%PDF-"))
        self.assertGreaterEqual(pdf.page_count(out), 2)
        self.assertFalse(any(p.name.startswith(".impression-") for p in src.parent.iterdir()))   # fichier temporaire supprimé

if __name__ == "__main__":
    unittest.main()
