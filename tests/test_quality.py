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


if __name__ == "__main__":
    unittest.main()
