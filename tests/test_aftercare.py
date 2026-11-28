"""Après la livraison : suivi automatique (remerciement, avis, offre suivante), retours clients."""
import json
import os
import unittest
from datetime import timedelta
from pathlib import Path

from test_commerce import Base

from ai_studio import aftercare, approvals, commerce
from ai_studio.db import iso, now
from ai_studio.tools import run_tool


class AftercareTest(Base):
    def setUp(self):
        super().setUp()
        os.environ.update(SMTP_HOST="smtp.gmail.com", SMTP_USER="studio@gmail.com", SMTP_PASSWORD="x")
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")
        self.n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe-lumiere.fr")

    def deliver(self, days_ago=4):
        commerce.mark_delivered(self.db, self.n, "livré")
        self.db.run("UPDATE sales SET delivered_at=? WHERE rowid=?", (iso(now() - timedelta(days=days_ago)), self.n))

    def test_followup_task_after_delay_only_once(self):
        self.deliver(days_ago=1)
        self.assertEqual(aftercare.due(self.cfg, self.db), [])                 # trop tôt
        self.deliver(days_ago=4)
        self.assertEqual(aftercare.due(self.cfg, self.db), [self.n])
        self.assertEqual(aftercare.due(self.cfg, self.db), [])                 # une seule fois
        t = self.db.one("SELECT * FROM tasks WHERE title LIKE 'Suivi après livraison%'")
        self.assertEqual((t["agent"], t["order_ref"]), ("support", self.n))
        self.assertIn("suivi_livraison", t["description"])

    def test_template_adds_next_offer_review_link_and_no_stop_line(self):
        self.cfg.data["aftercare"]["review_url"] = "https://g.page/r/exemple"
        self.deliver()
        aftercare.due(self.cfg, self.db)
        tid = self.db.one("SELECT id FROM tasks WHERE title LIKE 'Suivi après livraison%'")[0]
        from ai_studio.agents import ROLES
        from ai_studio.tools import ToolContext
        ctx = ToolContext(self.cfg, self.db, "support", self.db.task(tid), list(ROLES))
        out = run_tool("send_email", ctx, {"to": "marie@cafe-lumiere.fr", "reason": "suivi", "template": "suivi_livraison",
                                           "fields": {"commande": str(self.n), "salutation": "Madame Martin",
                                                      "remarque": "La première action du rapport, compléter vos horaires, prend dix minutes."}})
        self.assertIn("en attente de validation", out)
        p = json.loads(self.db.pending_approvals()[-1]["payload"])
        self.assertEqual(p["subject"], "Votre Diagnostic SEO local : tout vous convient ?")
        self.assertIn("Bonjour Madame Martin,", p["body"])
        self.assertIn("dix minutes.\n\nAvez-vous", p["body"])
        self.assertIn("https://g.page/r/exemple", p["body"])
        self.assertIn("nous réalisons aussi un Site vitrine", p["body"]); self.assertIn("390 €", p["body"])
        self.assertNotIn("STOP", p["body"])                                   # client, pas prospect
        self.assertNotIn("{", p["body"])

    def test_unhappy_client_no_upsell_and_owner_alerted(self):
        self.deliver()
        out = run_tool("record_feedback", self.ctx(), {"order": self.n, "feedback": "Le rapport ne parle pas de ma ville.", "satisfied": "non"})
        self.assertIn("propriétaire est prévenu", out)
        s = commerce.get_order(self.db, self.n)
        self.assertEqual(s["satisfied"], 0); self.assertIn("ma ville", s["feedback"])
        self.assertEqual(aftercare.due(self.cfg, self.db), [])                 # pas de message commercial automatique
        from ai_studio.email_templates import order_values
        self.assertEqual(order_values(self.cfg, self.db, self.n)["suite"], "")
        from ai_studio.missions import detail
        self.assertIn("ma ville", detail(self.db, "commande", self.n)["feedback"])

    def test_no_upsell_if_already_bought(self):
        from ai_studio.email_templates import order_values
        self.assertIn("Site vitrine", order_values(self.cfg, self.db, self.n)["suite"])
        commerce.test_order(self.cfg, self.db, "Site vitrine", "marie@cafe-lumiere.fr")
        self.assertEqual(order_values(self.cfg, self.db, self.n)["suite"], "")


if __name__ == "__main__":
    unittest.main()
