"""Bilan mensuel par offre (calcul sans API) et déclenchement le 1er du mois."""
import os
import unittest
import unittest.mock
from datetime import datetime, timedelta
from pathlib import Path

from test_commerce import Base

from ai_studio import commerce, prospects, reports
from ai_studio.db import iso, now
from ai_studio.tools import run_tool


class ReportTest(Base):
    def setUp(self):
        super().setUp()
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")

    def sale(self, sid, amount=89.0, live=1, offer="Diagnostic SEO local", email="a@cafe.fr"):
        oid = self.db.one("SELECT id FROM offers WHERE name=?", (offer,))[0]
        t = iso()
        self.db.run("INSERT INTO sales(id, created_at, amount, currency, customer_email, product, livemode, recorded_at, offer_id, stage) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", (sid, t, amount, "EUR", email, offer, live, t, oid, "a_livrer"))
        return commerce.order_number(self.db, sid)

    def test_monthly_figures(self):
        n1 = self.sale("cs_1")
        n2 = self.sale("cs_2", email="b@cafe.fr")
        self.sale("essai_1", live=0)
        t = self.db.add_task("Produire", "atelier", order_ref=n1)
        self.db.add_cost("atelier", t, "m", 1000, 1000, 0, 1.0)                      # 1 $ d'API sur la commande 1
        self.db.run("UPDATE sales SET stage='livree', delivered_at=?, due_at=?, satisfied=1 WHERE rowid=?",
                    (iso(now() + timedelta(hours=10)), iso(now() + timedelta(hours=72)), n1))
        self.db.run("UPDATE sales SET satisfied=0 WHERE rowid=?", (n2,))
        for e, st in (("p1@x.fr", "contacte"), ("p2@x.fr", "repondu"), ("p3@x.fr", "client"), ("p4@x.fr", "stop")):
            prospects.save(self.db, e, "https://x.fr", offer="Diagnostic SEO local")
            prospects.set_status(self.db, e, st, first_contact_at=iso())
        r = reports.monthly(self.cfg, self.db)
        o = next(x for x in r["offers"] if x["offer"] == "Diagnostic SEO local")
        self.assertEqual((o["sales"], o["test_orders"], o["revenue"]), (2, 1, 178.0))
        self.assertEqual((o["delivered"], o["late"], o["satisfied"], o["unsatisfied"]), (1, 0, 1, 1))
        self.assertAlmostEqual(o["api_eur"], 0.92)
        fees = 2 * (89 * 0.028 + 0.25)
        self.assertAlmostEqual(o["net"], round(178 - fees - 178 * 0.256 - 0.92 * 2 / 3, 2), places=2)
        self.assertEqual((o["prospects"], o["answered"], o["response_pct"], o["clients"]), (4, 3, 75.0, 1))
        md = reports.to_markdown(r)
        self.assertIn("| Diagnostic SEO local | 2 (+1 essai) | 178,00 €", md)
        self.assertIn("Bilan du mois", run_tool("monthly_report", self.ctx("finance"), {}))
        self.assertIn("AAAA-MM", run_tool("monthly_report", self.ctx("finance"), {"month": "novembre"}))

    def test_runs_once_on_new_month_with_finance_task(self):
        self.assertEqual(reports.prev_month(datetime(2027, 1, 1)), "2026-12")
        last = reports.prev_month()
        n = self.sale("cs_1")
        self.db.run("UPDATE sales SET created_at=? WHERE rowid=?", (f"{last}-15T10:00:00+00:00", n))
        path = reports.run_monthly(self.cfg, self.db)
        self.assertEqual(path, f"rapports/bilan-{last}.md")
        self.assertIn("89,00 €", (self.cfg.workspace / path).read_text(encoding="utf-8"))
        t = self.db.one("SELECT * FROM tasks WHERE agent='finance'")
        self.assertIn("AU PLUS 3 recommandations", t["description"])
        self.assertIsNone(reports.run_monthly(self.cfg, self.db))                  # une seule fois par mois

    def test_nothing_to_report_creates_no_task(self):
        self.db.run("DELETE FROM offers")
        self.assertIsNone(reports.run_monthly(self.cfg, self.db))
        self.assertIsNone(self.db.one("SELECT 1 FROM tasks"))


if __name__ == "__main__":
    unittest.main()
