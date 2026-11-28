"""Opérations externes fiables (audit FIN-03) : SMTP, Stripe et IMAP simulés, aucun envoi réel.

- une coupure au mauvais moment donne l'état « uncertain », jamais un renvoi à l'aveugle ;
- un succès est enregistré avant ses suites locales : une erreur de base ensuite ne le transforme pas en échec ;
- une exécution interrompue (programme arrêté) est détectée puis réconciliée ;
- Stripe : même clé d'idempotence à chaque essai ; email : retrouvé dans « Messages envoyés » par son Message-ID.
"""
import json
import os
import smtplib
import unittest.mock
import urllib.error
from datetime import timedelta

from ai_studio import approvals, commerce, notify
from ai_studio.db import iso, now
from test_commerce import Base


class FakeSMTP:
    fail_on = None             # "login", "send", "quit"
    sent: list = []

    def __init__(self, *a, **kw): pass
    def starttls(self): pass
    def login(self, *a):
        if FakeSMTP.fail_on == "login":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
    def send_message(self, msg):
        if FakeSMTP.fail_on == "send":
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        if FakeSMTP.fail_on == "refused":
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
        FakeSMTP.sent.append(msg)
    def quit(self):
        if FakeSMTP.fail_on == "quit":
            raise smtplib.SMTPServerDisconnected("closed")
    def close(self): pass


class FakeSentIMAP:
    def __init__(self, ids):
        self.ids, self.selected = ids, None
    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Messages envoy&AOk-s"']
    def select(self, box, readonly=False):
        self.selected = (box, readonly); return "OK", [b"1"]
    def search(self, charset, *crit):
        return "OK", [b"7" if crit[-1] in self.ids else b""]
    def logout(self): pass


class OperationsTest(Base):
    def setUp(self):
        super().setUp()
        os.environ.update(SMTP_HOST="smtp.gmail.com", SMTP_USER="studio@gmail.com", SMTP_PASSWORD="x")
        FakeSMTP.fail_on, FakeSMTP.sent = None, []
        p = unittest.mock.patch.object(approvals.smtplib, "SMTP", FakeSMTP)
        p.start(); self.addCleanup(p.stop)
        self.notes = []
        p = unittest.mock.patch.object(notify, "send", lambda cfg, db, t, m: self.notes.append(t) or True)
        p.start(); self.addCleanup(p.stop)

    def email(self, **extra):
        tid = self.db.add_task("Écrire", "ventes")
        return self.db.add_approval("ventes", tid, "send_email", {"to": "marie@cafe.fr", "subject": "Bonjour", "body": "Texte", **extra}, "r")

    def follow_ups(self):
        return self.db.q("SELECT * FROM tasks WHERE title LIKE 'Suite à la demande%'")

    # ------------------------------------------------------------ email
    def test_disconnect_during_send_is_uncertain_not_error(self):
        aid = self.email()
        out = approvals.decide(self.cfg, self.db, aid, True)
        FakeSMTP.fail_on = None
        self.assertIn("exécuté", out)
        aid = self.email()
        FakeSMTP.fail_on = "send"
        out = approvals.decide(self.cfg, self.db, aid, True)
        self.assertIn("INCERTAINE", out)
        self.assertEqual(self.db.approval(aid)["status"], "uncertain")
        self.assertEqual(len(self.follow_ups()), 1)              # seulement celle du 1er email : l'agent ne refait rien
        self.assertTrue(any("incertaine" in n for n in self.notes))

    def test_failure_before_sending_is_a_certain_error(self):
        FakeSMTP.fail_on = "login"
        aid = self.email()
        self.assertIn("échoué", approvals.decide(self.cfg, self.db, aid, True))
        self.assertEqual(self.db.approval(aid)["status"], "error")
        FakeSMTP.fail_on = "refused"
        aid = self.email()
        approvals.decide(self.cfg, self.db, aid, True)
        self.assertEqual(self.db.approval(aid)["status"], "error")

    def test_error_after_acceptance_is_still_a_success(self):
        FakeSMTP.fail_on = "quit"
        aid = self.email()
        self.assertIn("exécuté", approvals.decide(self.cfg, self.db, aid, True))
        self.assertEqual(self.db.approval(aid)["status"], "executed")

    def test_local_follow_up_failure_does_not_turn_success_into_error(self):
        aid = self.email(order=1)
        with unittest.mock.patch.object(commerce, "mark_delivered", side_effect=RuntimeError("base verrouillée")):
            approvals.decide(self.cfg, self.db, aid, True)
        self.assertEqual(self.db.approval(aid)["status"], "executed")      # pas « error » : il ne sera pas renvoyé
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.assertTrue(self.db.one("SELECT 1 FROM events WHERE message LIKE '%suite locale a échoué%'"))

    def test_message_id_saved_before_sending(self):
        aid = self.email()
        seen = []
        def spy(cfg, payload):
            seen.append(json.loads(self.db.approval(aid)["payload"]).get("message_id"))   # déjà en base au moment de l'envoi
            return "Email envoyé"
        with unittest.mock.patch.dict(approvals.EXECUTORS, {"send_email": spy}):
            approvals.decide(self.cfg, self.db, aid, True)
        self.assertTrue(seen[0].startswith(f"<studio-{aid}-") and seen[0].endswith("@gmail.com>"))
        aid2 = self.email()
        approvals.decide(self.cfg, self.db, aid2, True)
        self.assertEqual(FakeSMTP.sent[0]["Message-ID"], json.loads(self.db.approval(aid2)["payload"])["message_id"])

    # ------------------------------------------------------------ programme arrêté pendant l'envoi
    def interrupted(self, minutes=30, **extra):
        aid = self.email(**extra)
        mid = f"<studio-{aid}-abc@gmail.com>"
        p = json.loads(self.db.approval(aid)["payload"]); p["message_id"] = mid
        self.db.set_approval(aid, status="processing", exec_key="abc", started_at=iso(now() - timedelta(minutes=minutes)),
                             payload=json.dumps(p))
        return aid, mid

    def test_interrupted_email_found_in_sent_folder_is_marked_sent(self):
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe.fr") if self.offers() else None
        aid, mid = self.interrupted(order=n)
        r = approvals.reconcile(self.cfg, self.db, sent_lookup=lambda m: m == mid)
        self.assertEqual((r["stuck"], r["resolved"]), (1, 1))
        self.assertEqual(self.db.approval(aid)["status"], "executed")
        self.assertEqual(commerce.get_order(self.db, n)["stage"], "livree")  # suite locale faite
        self.assertEqual(FakeSMTP.sent, [])                                  # rien renvoyé

    def test_interrupted_email_not_found_waits_for_owner_and_is_never_resent(self):
        aid, _ = self.interrupted()
        for _ in range(3):
            r = approvals.reconcile(self.cfg, self.db, sent_lookup=lambda m: None)
        self.assertEqual(self.db.approval(aid)["status"], "uncertain")
        self.assertEqual(r["waiting"], 1)
        self.assertEqual(FakeSMTP.sent, [])
        from ai_studio.server import approvals as listing
        self.assertTrue(any(a.get("uncertain") and a["id"] == aid for a in listing(self.cfg, self.db)))

    def test_recent_processing_is_left_alone(self):
        aid, _ = self.interrupted(minutes=1)                                 # peut-être encore en cours
        approvals.reconcile(self.cfg, self.db, sent_lookup=lambda m: None)
        self.assertEqual(self.db.approval(aid)["status"], "processing")

    def test_owner_resolution(self):
        from ai_studio.server import resolve_approval
        aid, mid = self.interrupted()
        approvals.reconcile(self.cfg, self.db, sent_lookup=lambda m: None)
        self.assertIn("Remise", resolve_approval(self.cfg, self.db, aid, {"done": False})["message"])
        a = self.db.approval(aid)
        self.assertEqual((a["status"], a["exec_key"]), ("pending", None))
        approvals.decide(self.cfg, self.db, aid, True)                       # nouvel essai : nouvelle clé, nouveau Message-ID
        self.assertNotEqual(FakeSMTP.sent[0]["Message-ID"], mid)
        aid2, _ = self.interrupted()
        approvals.reconcile(self.cfg, self.db, sent_lookup=lambda m: None)
        self.assertIn("faite", resolve_approval(self.cfg, self.db, aid2, {"done": True})["message"])
        self.assertEqual(self.db.approval(aid2)["status"], "executed")
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_sent_folder_lookup_via_imap(self):
        found = approvals.sent_copy_exists("<studio-1-x@gmail.com>", connect=lambda: FakeSentIMAP({"<studio-1-x@gmail.com>"}))
        self.assertTrue(found)
        imap = FakeSentIMAP(set())
        self.assertFalse(approvals.sent_copy_exists("<autre@gmail.com>", connect=lambda: imap))
        self.assertEqual(imap.selected, ('"[Gmail]/Messages envoy&AOk-s"', True))   # dossier \Sent, en lecture seule

    # ------------------------------------------------------------ Stripe
    def offers(self):
        from pathlib import Path
        return commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")

    def test_stripe_retry_uses_same_idempotency_keys(self):
        self.offers()
        os.environ["STRIPE_SECRET_KEY"] = "rk_test"
        oid = self.db.one("SELECT id FROM offers WHERE name='Diagnostic SEO local'")[0]
        aid = self.db.add_approval("ventes", None, "stripe_payment_link",
                                   {"amount_eur": 89, "product_name": "Diagnostic SEO local", "offer_id": oid}, "vendre")
        keys = []
        def down(method, path, params, idempotency_key=None):
            keys.append(idempotency_key)
            raise urllib.error.URLError("timed out")
        with unittest.mock.patch.object(approvals, "stripe_request", down):
            self.assertIn("INCERTAINE", approvals.decide(self.cfg, self.db, aid, True))
        answers = iter([{"id": "price_1"}, {"id": "plink_1", "url": "https://buy.stripe.com/test_1"}])
        def up(method, path, params, idempotency_key=None):
            keys.append(idempotency_key)
            return next(answers)
        with unittest.mock.patch.object(approvals, "stripe_request", up):
            r = approvals.reconcile(self.cfg, self.db)
        self.assertEqual(r["resolved"], 1)
        self.assertEqual(self.db.approval(aid)["status"], "executed")
        self.assertEqual(keys[0], keys[1])                                   # même clé « -price » au nouvel essai
        self.assertTrue(keys[0].endswith("-price") and keys[2].endswith("-link"))
        self.assertEqual(self.db.one("SELECT payment_link_id FROM offers WHERE id=?", (oid,))[0], "plink_1")

    def test_stripe_refusal_is_certain_and_retries_are_capped(self):
        os.environ["STRIPE_SECRET_KEY"] = "rk_test"
        aid = self.db.add_approval("ventes", None, "stripe_payment_link", {"amount_eur": 89, "product_name": "X"}, "r")
        refused = urllib.error.HTTPError("u", 400, "Bad Request", {}, None)
        with unittest.mock.patch.object(approvals, "stripe_request", unittest.mock.Mock(side_effect=refused)):
            approvals.decide(self.cfg, self.db, aid, True)
        self.assertEqual(self.db.approval(aid)["status"], "error")
        aid = self.db.add_approval("ventes", None, "stripe_payment_link", {"amount_eur": 89, "product_name": "X"}, "r")
        down = unittest.mock.Mock(side_effect=urllib.error.URLError("down"))
        with unittest.mock.patch.object(approvals, "stripe_request", down):
            approvals.decide(self.cfg, self.db, aid, True)
            for _ in range(8):
                approvals.reconcile(self.cfg, self.db)
        self.assertEqual(self.db.approval(aid)["status"], "uncertain")
        self.assertEqual(down.call_count, 1 + approvals.MAX_RECONCILE)       # essais plafonnés, puis le propriétaire tranche


if __name__ == "__main__":
    unittest.main()
