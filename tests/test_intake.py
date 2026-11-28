"""Corrections après l'audit interne du PDG : fiche d'offre complète, méthode lisible, collecte des informations du
client et délai qui démarre à leur réception."""
import json
import os
import unittest
from pathlib import Path

from test_commerce import Base, FakeIMAP, mail

from ai_studio import commerce
from ai_studio.tools import run_tool


class IntakeTest(Base):
    def setUp(self):
        super().setUp()
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")

    def test_offer_listing_is_explicit(self):
        out = run_tool("list_offers", self.ctx("recherche"), {})
        self.assertIn("Statut : VALIDÉE par le propriétaire", out)
        self.assertIn("Lien de paiement : pas encore créé", out)
        self.assertIn("Marge estimée par vente : Prix 89,00 €", out)
        self.assertIn("Méthode de production : oui", out)
        self.assertIn("après réception des informations complètes", out)
        m = run_tool("offer_method", self.ctx("juridique"), {"offer": "diagnostic seo local"})
        self.assertIn("Score de présence locale", m)
        self.assertIn("Pas de méthode écrite", run_tool("offer_method", self.ctx(), {"offer": "Site vitrine"}))
        self.assertIn("Offre inconnue", run_tool("offer_method", self.ctx(), {"offer": "Autre"}))

    def test_paid_order_asks_information_and_clock_waits(self):
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe-lumiere.fr")
        s = commerce.get_order(self.db, n)
        self.assertEqual((s["stage"], s["due_at"], s["info_received_at"]), ("attente_client", None, None))
        t = self.db.one("SELECT * FROM tasks WHERE order_ref=? AND agent='support'", (n,))
        from ai_studio.agents import ROLES
        from ai_studio.tools import ToolContext
        ctx = ToolContext(self.cfg, self.db, "support", self.db.task(t["id"]), list(ROLES))
        out = run_tool("send_email", ctx, {"to": "marie@cafe-lumiere.fr", "reason": "infos", "template": "demande_informations",
                                           "fields": {"commande": str(n), "salutation": "Madame Martin"}})
        self.assertIn("en attente de validation", out)
        p = json.loads(self.db.pending_approvals()[-1]["payload"])
        self.assertEqual(p["subject"], "Votre commande Diagnostic SEO local : les informations dont nous avons besoin")
        self.assertIn("Nom et adresse de l'établissement", p["body"])
        self.assertIn("Le délai de livraison (3 jours) démarre à la réception de ces informations.", p["body"])
        self.assertIn("pas encore reçues", commerce.delivery_brief(self.db, n))
        # la réponse du client fait démarrer le délai
        os.environ.update(SMTP_HOST="smtp.gmail.com", SMTP_USER="studio@gmail.com", SMTP_PASSWORD="x")
        imap = FakeIMAP({1: mail("Old <old@x.fr>", "Ancien", "vieux", "<1@x>")})
        commerce.sync_inbox(self.cfg, self.db, connect=lambda: imap)
        imap.msgs[2] = mail("Marie <marie@cafe-lumiere.fr>", "Re: Votre commande", "Café Lumière, 3 rue X, Lyon ; site cafe-lumiere.fr", "<2@x>")
        commerce.sync_inbox(self.cfg, self.db, connect=lambda: imap)
        s = commerce.get_order(self.db, n)
        self.assertEqual(s["stage"], "a_livrer"); self.assertIsNotNone(s["info_received_at"]); self.assertIsNotNone(s["due_at"])

    def test_test_order_with_infos_starts_clock_immediately(self):
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe-lumiere.fr", "Café Lumière, Lyon, cafe-lumiere.fr")
        s = commerce.get_order(self.db, n)
        self.assertEqual(s["stage"], "a_livrer"); self.assertIsNotNone(s["due_at"])
        self.assertIn("Café Lumière", s["owner_notes"])
        self.assertIsNone(self.db.one("SELECT 1 FROM tasks WHERE order_ref=? AND agent='support'", (n,)))

    def test_status_change_starts_clock(self):
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe-lumiere.fr")
        self.db.run("UPDATE sales SET stage='attente_client' WHERE rowid=?", (n,))
        run_tool("set_order_status", self.ctx("chef_projet"), {"order": n, "status": "a_livrer", "note": "infos reçues par téléphone"})
        self.assertIsNotNone(commerce.get_order(self.db, n)["due_at"])


if __name__ == "__main__":
    unittest.main()
