"""Boîte mail sans perte (audit OPS-02) : IMAP simulé.

- le curseur IMAP n'avance qu'avec l'enregistrement du message (même transaction) ;
- un message enregistré mais pas traité (arrêt, erreur) est repris au passage suivant, sans tâche en double ;
- les messages bloqués par le plafond du jour sont repris ensuite (avant : « new » pour toujours).
"""
import os
import unittest.mock

from ai_studio import commerce
from test_commerce import Base, FakeIMAP, mail


class InboxRecoveryTest(Base):
    def setUp(self):
        super().setUp()
        os.environ.update(SMTP_HOST="smtp.gmail.com", SMTP_USER="studio@gmail.com", SMTP_PASSWORD="x")
        self.db.kv_set("inbox_last_uid", "1"); self.db.kv_set("inbox_uidvalidity", "7")
        self.imap = FakeIMAP({1: b"", 2: mail("Marie <marie@cafe.fr>", "Question 1", "Bonjour ?", "<2@x>"),
                              3: mail("Paul <paul@bar.fr>", "Question 2", "Combien ?", "<3@x>")})

    def sync(self):
        return commerce.sync_inbox(self.cfg, self.db, connect=lambda: self.imap)

    def tasks(self):
        return self.db.q("SELECT * FROM tasks WHERE title LIKE 'Répondre à%' ORDER BY id")

    def test_crash_while_reading_does_not_skip_the_message(self):
        real = commerce.bounce_recipients
        calls = []
        def boom(*a, **k):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("coupure pendant la lecture du 2e message")
            return real(*a, **k)
        with unittest.mock.patch.object(commerce, "bounce_recipients", boom):
            with self.assertRaises(RuntimeError):
                self.sync()
        self.assertEqual(self.db.kv_get("inbox_last_uid"), "2")              # le 3e n'est pas marqué lu
        self.assertEqual([r["subject"] for r in self.db.q("SELECT subject FROM inbox")], ["Question 1"])
        self.sync()
        self.assertEqual(self.db.kv_get("inbox_last_uid"), "3")
        self.assertEqual(len(self.tasks()), 2)                                # les deux messages ont leur tâche

    def test_crash_while_processing_is_resumed(self):
        real = commerce._queue_reply_task
        with unittest.mock.patch.object(commerce, "_queue_reply_task", side_effect=RuntimeError("base verrouillée")):
            with self.assertRaises(RuntimeError):
                self.sync()
        self.assertEqual(self.db.kv_get("inbox_last_uid"), "3")              # messages enregistrés…
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM inbox WHERE status='received'")[0], 2)   # … pas encore traités
        r = self.sync()
        self.assertEqual((r["new"], r["queued"]), (0, 2))
        self.assertEqual(len(self.tasks()), 2)
        self.assertTrue(real)

    def test_no_duplicate_task_if_stopped_after_creating_it(self):
        self.sync()
        first = [t["id"] for t in self.tasks()]
        self.db.run("UPDATE inbox SET status='received', task_id=NULL")       # arrêt juste avant la mise à jour du message
        self.sync()
        self.assertEqual([t["id"] for t in self.tasks()], first)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM inbox WHERE status='queued' AND task_id IS NOT NULL")[0], 2)

    def test_legacy_messages_stuck_by_the_cap_are_processed(self):
        self.db.run("INSERT INTO inbox(uid, message_id, from_addr, from_name, subject, body, status, recorded_at) "
                    "VALUES (0, '<old@x>', 'jean@resto.fr', 'Jean', 'Ancienne question', 'Toujours dispo ?', 'new', '2026-11-20T10:00:00+00:00')")
        self.sync()
        self.assertTrue(any(t["title"].startswith("Répondre à Jean") for t in self.tasks()))
        self.assertEqual(self.db.one("SELECT status FROM inbox WHERE message_id='<old@x>'")[0], "queued")

    def test_bounce_processed_once(self):
        self.db.add_approval("ventes", None, "send_email", {"to": "faux@inexistant.fr", "subject": "Une piste", "body": "b"}, "r")
        self.db.run("UPDATE approvals SET status='executed', decided_at=?", (commerce.iso(),))
        self.imap.msgs[4] = mail("Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "Delivery Status Notification (Failure)",
                                 "Address not found: faux@inexistant.fr — 550 5.1.1 user unknown", "<4@x>",
                                 X_Failed_Recipients="faux@inexistant.fr")
        self.sync()
        self.assertEqual(self.db.one("SELECT status FROM inbox WHERE message_id='<4@x>'")[0], "bounce")
        self.assertEqual(self.db.one("SELECT status FROM approvals")[0], "bounced")
        n = self.db.one("SELECT COUNT(*) FROM tasks WHERE title LIKE 'Email non distribué%'")[0]
        self.db.run("UPDATE inbox SET status='received' WHERE message_id='<4@x>'")
        self.sync()
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM tasks WHERE title LIKE 'Email non distribué%'")[0], n)


if __name__ == "__main__":
    import unittest
    unittest.main()
