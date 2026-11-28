"""Fichier prospects : enregistrement, garde-fous d'envoi, relance unique, réponses, opposition (sans coût, sans réseau)."""
import json
import os
import unittest
import unittest.mock
from datetime import timedelta

from test_commerce import Base, FakeIMAP, mail

from ai_studio import approvals, commerce, prospects
from ai_studio.db import iso, now
from ai_studio.tools import run_tool


class ProspectsTest(Base):
    def setUp(self):
        super().setUp()
        os.environ.update(SMTP_HOST="smtp.gmail.com", SMTP_USER="studio@gmail.com", SMTP_PASSWORD="x")

    def send(self, to="contact@ax-07.fr", **kw):
        args = {"to": to, "subject": "Une piste", "body": "Bonjour, …", "reason": "prospection", **kw}
        return run_tool("send_email", self.ctx(), args)

    def approve_last(self, ok=True):
        aid = self.db.pending_approvals()[-1]["id"]
        with unittest.mock.patch.dict(approvals.EXECUTORS, {"send_email": lambda cfg, p: "Email envoyé"}):
            return approvals.decide(self.cfg, self.db, aid, ok, "" if ok else "non")

    def test_send_requires_registered_prospect_with_source(self):
        self.assertIn("non enregistré", self.send())
        self.assertIn("Source invalide", run_tool("save_prospect", self.ctx("ventes"), {"email": "contact@ax-07.fr", "source": "le site"}))
        out = run_tool("save_prospect", self.ctx("ventes"), {"email": "Contact@AX-07.fr", "source": "https://www.ax-07.fr/mentions-legales",
                                                     "company": "ax-07", "site": "ax-07.fr"})
        self.assertIn("enregistré", out)
        self.assertIn("en attente de validation", self.send())
        p = prospects.get(self.db, "contact@ax-07.fr")
        self.assertEqual(p["status"], "a_valider")
        self.assertIn("attend déjà", self.send())                                  # pas de doublon
        self.assertIn("déjà connu", run_tool("save_prospect", self.ctx("ventes"), {"email": "contact@ax-07.fr", "source": "https://x.fr"}))

    def test_rejected_email_restores_status(self):
        prospects.save(self.db, "a@b.fr", "https://b.fr")
        self.send("a@b.fr")
        self.approve_last(ok=False)
        self.assertEqual(prospects.get(self.db, "a@b.fr")["status"], "nouveau")

    def test_full_cycle_contact_single_followup_then_closed(self):
        prospects.save(self.db, "contact@ax-07.fr", "https://www.ax-07.fr/")
        self.send()
        self.approve_last()
        p = prospects.get(self.db, "contact@ax-07.fr")
        self.assertEqual(p["status"], "contacte")
        self.assertEqual(p["next_action_at"][:10], iso(now() + timedelta(days=7))[:10])
        self.assertIn("pas encore répondu", self.send())                           # pas de second email sans réponse
        self.db.run("UPDATE approvals SET decided_at=?", (iso(now() - timedelta(days=8)),))      # 8 jours plus tard
        self.assertIn("déjà contacté", self.send())                                # et jamais de second premier email
        self.assertEqual(prospects.due_followups(self.cfg, self.db), {"followups": [], "closed": []})   # trop tôt
        self.db.run("UPDATE prospects SET next_action_at=? WHERE email=?", (iso(now() - timedelta(minutes=1)), "contact@ax-07.fr"))
        self.assertEqual(prospects.due_followups(self.cfg, self.db)["followups"], ["contact@ax-07.fr"])
        t = self.db.one("SELECT * FROM tasks WHERE title LIKE 'Relance prospect%'")
        self.assertEqual(t["agent"], "ventes"); self.assertIn("template « relance »", t["description"])
        self.assertEqual(prospects.due_followups(self.cfg, self.db)["followups"], [])   # une seule tâche
        out = self.send(template="relance", fields={"objet_initial": "Une piste", "date_initiale": "28 novembre",
                                                    "sujet": "un diagnostic de votre visibilité locale",
                                                    "rappel": "Le diagnostic vous donnerait 10 actions concrètes et priorisées."},
                        subject="", body="")
        self.assertIn("en attente de validation", out)
        self.approve_last()
        self.assertEqual(prospects.get(self.db, "contact@ax-07.fr")["status"], "relance")
        self.db.run("UPDATE approvals SET decided_at=?", (iso(now() - timedelta(days=8)),))
        self.assertIn("relance impossible", self.send(template="relance", fields={}))   # une seule relance
        self.db.run("UPDATE prospects SET next_action_at=? WHERE email=?", (iso(now() - timedelta(minutes=1)), "contact@ax-07.fr"))
        self.assertEqual(prospects.due_followups(self.cfg, self.db)["closed"], ["contact@ax-07.fr"])
        self.assertEqual(prospects.get(self.db, "contact@ax-07.fr")["status"], "sans_suite")

    def test_replies_stop_refusal_and_quoted_text(self):
        q = "\n\nLe 28 nov. 2026 à 10:00, Studio <studio@gmail.com> a écrit :\n> Si vous ne souhaitez plus recevoir de message, répondez simplement « STOP »."
        for e in ("a@b.fr", "c@d.fr", "e@f.fr"):
            prospects.save(self.db, e, "https://x.fr")
            prospects.set_status(self.db, e, "contacte")
        self.assertEqual(prospects.on_reply(self.db, "a@b.fr", "Bonjour, quel est le délai exact ?" + q), "repondu")   # « STOP » cité ≠ opposition
        self.assertEqual(prospects.on_reply(self.db, "c@d.fr", "STOP" + q), "stop")
        self.assertEqual(prospects.on_reply(self.db, "e@f.fr", "Merci mais pas intéressé pour le moment." + q), "refus")
        self.assertIsNone(prospects.on_reply(self.db, "inconnu@x.fr", "STOP"))
        self.assertFalse(prospects.set_status(self.db, "c@d.fr", "interesse"))       # opposition définitive
        self.assertIn("plus être contacté", self.send("c@d.fr"))
        self.assertIn("plus être contacté", self.send("e@f.fr"))
        self.assertIn("réponds dans le fil", self.send("a@b.fr"))
        self.assertIn("Intéressé", run_tool("set_prospect_status", self.ctx(), {"email": "a@b.fr", "status": "interesse", "note": "veut un devis"}))

    def test_inbox_routes_prospect_reply_and_handles_stop(self):
        imap = FakeIMAP({1: mail("Old <old@x.fr>", "Ancien", "vieux", "<1@x>")})
        commerce.sync_inbox(self.cfg, self.db, connect=lambda: imap)                 # premier relevé : historique ignoré
        for e in ("paul@boulangerie.fr", "zoe@cafe.fr"):
            prospects.save(self.db, e, "https://x.fr")
            prospects.set_status(self.db, e, "contacte")
        imap.msgs.update({2: mail("Paul <paul@boulangerie.fr>", "Re: Une piste", "Bonjour, combien de temps ?", "<2@x>"),
                          3: mail("Zoé <zoe@cafe.fr>", "Re: Une piste", "Stop, merci.", "<3@x>")})
        r = commerce.sync_inbox(self.cfg, self.db, connect=lambda: imap)
        self.assertEqual((r["queued"], r["ignored"]), (1, 1))
        t = self.db.one("SELECT * FROM tasks WHERE title LIKE 'Répondre à Paul%'")
        self.assertEqual(t["agent"], "ventes"); self.assertIn("PROSPECT", t["description"])
        self.assertEqual(prospects.get(self.db, "paul@boulangerie.fr")["status"], "repondu")
        self.assertEqual(prospects.get(self.db, "zoe@cafe.fr")["status"], "stop")
        self.assertIsNone(self.db.one("SELECT 1 FROM tasks WHERE title LIKE 'Répondre à Zoé%'"))

    def test_bounce_marks_invalid_and_payment_marks_client(self):
        prospects.save(self.db, "a@b.fr", "https://b.fr")
        self.send("a@b.fr"); self.approve_last()
        commerce.handle_bounce(self.cfg, self.db, ["a@b.fr"], "boîte inexistante")
        self.assertEqual(prospects.get(self.db, "a@b.fr")["status"], "invalide")
        prospects.save(self.db, "client@exemple.fr", "https://exemple.fr")
        sale = {"id": "cs_1", "created_at": iso(), "amount": 89.0, "currency": "EUR", "customer_email": "client@exemple.fr",
                "customer_name": "Client", "product": "x", "payment_link": None, "livemode": 1}
        self.db.run("INSERT INTO sales(id, created_at, amount, currency, customer_email, customer_name, product, payment_link, livemode, "
                    "recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (*sale.values(), iso()))
        commerce.open_order(self.cfg, self.db, sale)
        self.assertEqual(prospects.get(self.db, "client@exemple.fr")["status"], "client")


if __name__ == "__main__":
    unittest.main()
