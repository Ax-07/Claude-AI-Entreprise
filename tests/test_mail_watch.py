"""Surveillance de la boîte mail en temps réel (IMAP IDLE simulé) et notification à l'arrivée d'un email."""
import os
import threading
import unittest
import unittest.mock

from ai_studio import commerce, mail_watch
from test_commerce import Base, FakeIMAP, mail


class FakeIdle:
    def __init__(self, lines):
        self.lines, self.sent, self.selected = list(lines), [], None

    def _new_tag(self):
        return b"A7"

    def send(self, data):
        self.sent.append(data)

    def readline(self):
        x = self.lines.pop(0) if self.lines else b""
        if isinstance(x, Exception):
            raise x
        return x

    def select(self, box, readonly=False):
        self.selected = (box, readonly)
        return "OK", [b"1"]

    def logout(self):
        pass


class MailWatchTest(Base):
    def test_idle_detects_new_mail(self):
        M = FakeIdle([b"+ idling", b"* 5 EXISTS", b"A7 OK IDLE terminated"])
        self.assertTrue(mail_watch.wait_for_mail(M))
        self.assertEqual(M.sent, [b"A7 IDLE\r\n", b"DONE\r\n"])

    def test_idle_timeout_without_mail(self):
        M = FakeIdle([b"+ idling", TimeoutError(), b"A7 OK IDLE terminated"])
        self.assertFalse(mail_watch.wait_for_mail(M, timeout=0.01))
        self.assertEqual(M.sent[-1], b"DONE\r\n")

    def test_watch_syncs_on_arrival_and_survives_errors(self):
        os.environ.update(SMTP_HOST="smtp.gmail.com", SMTP_USER="studio@gmail.com", SMTP_PASSWORD="x")
        conns = [FakeIdle([b"+ idling", b"* 3 EXISTS", b"A7 OK"]), RuntimeError("réseau coupé")]
        def connect():
            c = conns.pop(0)
            if isinstance(c, Exception):
                raise c
            return c
        synced = []
        stop = threading.Event()
        with unittest.mock.patch.object(commerce, "sync_inbox", lambda cfg, db: synced.append(1)), \
             unittest.mock.patch.object(stop, "wait", lambda s: None):
            mail_watch.watch(self.cfg, lambda: self.db, connect=connect, stop=stop, max_loops=2)
        self.assertEqual(synced, [1])                                    # un relevé dès l'arrivée
        self.assertIn("interrompue", self.db.one("SELECT message FROM events ORDER BY id DESC LIMIT 1")[0])

    def test_owner_notified_when_email_arrives(self):
        os.environ.update(SMTP_HOST="smtp.gmail.com", SMTP_USER="studio@gmail.com", SMTP_PASSWORD="x")
        self.db.kv_set("inbox_last_uid", "0"); self.db.kv_set("inbox_uidvalidity", "7")
        sent = []
        with unittest.mock.patch.object(commerce, "_notify", lambda cfg, db, t, m, k: sent.append((t, m, k))):
            commerce.sync_inbox(self.cfg, self.db, connect=lambda: FakeIMAP({
                1: mail("Marie <marie@cafe.fr>", "Devis pour un site", "Bonjour", "<m@x>"),
                2: mail("Newsletter <news@shop.fr>", "Promo", "x", "<n@x>", List_Unsubscribe="<mailto:u@shop.fr>")}))
        self.assertEqual([(t, k) for t, m, k in sent], [("Email reçu de Marie", "emails")])   # pas pour la newsletter
        self.assertIn("Devis pour un site", sent[0][1])

    def test_not_started_without_mailbox_or_when_disabled(self):
        self.assertIsNone(mail_watch.start(self.cfg, lambda: self.db))   # pas de boîte mail configurée


if __name__ == "__main__":
    unittest.main()
