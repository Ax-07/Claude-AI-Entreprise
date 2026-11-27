"""Notifications Windows et sauvegardes (sans Windows ni appel aux IA : le lanceur de toasts est simulé)."""
import os
import shutil
import sqlite3
import tempfile
import unittest
import unittest.mock
import xml.dom.minidom
from pathlib import Path

from ai_studio import backup, notify
from ai_studio.config import load_config
from ai_studio.db import DB


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.cfg = load_config(self.dir)
        self.cfg.data["security"]["daily_review"] = False   # revue quotidienne testée à part
        self.cfg.ensure_dirs()
        (self.dir / ".gitignore").write_text(".env\nconfig.json\ndata/\n", encoding="utf-8")   # installation saine
        self.db = DB(self.cfg.db_path)
        self.addCleanup(self.db.conn.close)
        self.toasts = []
        p = unittest.mock.patch.object(notify, "runner", lambda xml: self.toasts.append(xml) or True)
        p.start(); self.addCleanup(p.stop)


class NotifyTest(Base):
    def approval(self, action="send_email", agent="ventes", reason="Relance client"):
        return self.db.add_approval(agent, None, action, {"to": "a@b.fr", "subject": "s", "body": "b"}, reason)

    def test_new_approval_notified_once(self):
        self.approval()
        sent = notify.check(self.cfg, self.db, {})
        self.assertEqual(sent, ["Validation demandée : Envoi d'email"])
        self.assertIn("Relance client", self.toasts[0])
        self.assertEqual(notify.check(self.cfg, self.db, {}), [])      # pas de doublon au cycle suivant
        self.approval("stripe_payment_link"); self.approval("decision", "pdg")
        sent = notify.check(self.cfg, self.db, {})
        self.assertEqual(sent, ["2 nouvelles validations demandées"])
        self.assertIn("3 en attente au total", self.toasts[-1])

    def test_agent_text_cannot_break_toast(self):
        self.approval(reason='<script>"&\'; Remove-Item C:\\ -Recurse')
        notify.check(self.cfg, self.db, {})
        doc = xml.dom.minidom.parseString(self.toasts[0])               # XML toujours valide
        texts = [t.firstChild.data for t in doc.getElementsByTagName("text")]
        self.assertIn("<script>", texts[1])
        self.assertNotIn("Remove-Item", notify.PS_SCRIPT)               # le texte ne passe jamais dans la commande

    def test_toast_opens_dashboard(self):
        self.db.kv_set("dashboard_url", "http://127.0.0.1:9000")
        self.approval()
        notify.check(self.cfg, self.db, {})
        doc = xml.dom.minidom.parseString(self.toasts[0])
        self.assertEqual(doc.documentElement.getAttribute("launch"), "http://127.0.0.1:9000")

    def test_budget_and_error_notified_once_until_resolved(self):
        s = {"paused": "Plafond du jour atteint", "error": None}
        self.assertEqual(notify.check(self.cfg, self.db, s), ["Moteur en pause : budget atteint"])
        self.assertEqual(notify.check(self.cfg, self.db, s), [])
        notify.check(self.cfg, self.db, {"paused": None})               # résolu
        self.assertEqual(notify.check(self.cfg, self.db, s), ["Moteur en pause : budget atteint"])
        self.assertEqual(notify.check(self.cfg, self.db, {"error": "API 401"}), ["Moteur bloqué : erreur de configuration"])

    def test_can_be_disabled(self):
        self.cfg.data["notifications"]["windows"] = False
        self.approval()
        notify.check(self.cfg, self.db, {})
        self.assertEqual(self.toasts, [])

    def test_failing_powershell_never_raises(self):
        def boom(xml):
            raise FileNotFoundError("powershell.exe")
        with unittest.mock.patch.object(notify, "runner", boom):
            self.approval()
            self.assertEqual(notify.check(self.cfg, self.db, {}), [])
        self.assertIn("Notification impossible", self.db.q("SELECT message FROM events ORDER BY id DESC LIMIT 1")[0][0])

    def test_powershell_error_is_reported(self):
        class R:
            returncode, stdout, stderr = 1, b"", "Accès refusé".encode()
        with unittest.mock.patch.object(notify.subprocess, "run", return_value=R()), \
             unittest.mock.patch.object(notify, "runner", notify._windows_runner):
            self.assertFalse(notify.send(self.cfg, self.db, "t", "m"))
        self.assertIn("Accès refusé", self.db.q("SELECT message FROM events ORDER BY id DESC LIMIT 1")[0][0])
        self.assertTrue(notify.PS_SCRIPT.startswith("$ErrorActionPreference = 'Stop'"))

    def test_engine_cycle_sends_notifications(self):
        from ai_studio.orchestrator import Studio
        self.approval()
        st = Studio(self.cfg, self.db, llm=None)
        st.cycle()                       # aucune tâche : le cycle ne touche pas au modèle
        self.assertEqual(len(self.toasts), 1)
        self.assertTrue((self.dir / "data" / "sauvegardes").exists())


class BackupTest(Base):
    def test_daily_backup_is_consistent_and_once_per_day(self):
        self.db.add_objective("Objectif important")
        (self.cfg.workspace / "rapport.md").write_text("contenu", encoding="utf-8")
        dest = backup.daily(self.cfg, self.db)
        self.assertIsNotNone(dest)
        self.assertIsNone(backup.daily(self.cfg, self.db))
        copy = sqlite3.connect(str(dest / "studio.db"))
        self.assertEqual(copy.execute("SELECT text FROM objectives").fetchone()[0], "Objectif important")
        copy.close()
        self.assertTrue((dest / "livrables.zip").exists())

    def test_prune_keeps_most_recent(self):
        self.cfg.data["backup"]["keep"] = 3
        for d in ("2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"):
            backup.make_backup(self.cfg, self.db, d)
        self.assertEqual([b["name"] for b in backup.list_backups(self.cfg)], ["2026-11-05", "2026-11-04", "2026-11-03"])

    def test_restore_keeps_previous_db(self):
        self.db.add_objective("Avant")
        backup.make_backup(self.cfg, self.db, "sauv")
        self.db.add_objective("Après")
        self.db.conn.close()
        backup.restore(self.cfg, "sauv")
        db2 = DB(self.cfg.db_path)
        self.assertEqual([r[0] for r in db2.q("SELECT text FROM objectives")], ["Avant"])
        db2.conn.close()
        self.assertTrue(any(p.name.startswith("studio.avant-restauration") for p in self.cfg.db_path.parent.iterdir()))
        self.db = DB(":memory:")         # pour le nettoyage

    def test_disabled(self):
        self.cfg.data["backup"]["enabled"] = False
        self.assertIsNone(backup.daily(self.cfg, self.db))


class MemoryCliTest(Base):
    def test_search_forget_and_owner_priority(self):
        from ai_studio import __main__ as cli
        bad = self.db.remember("support", "commande 18 envoi", "Aucun outil d'envoi direct d'email.")
        self.db.remember("pdg", "offre", "Diagnostic SEO local à 250 €.")
        out = []
        with unittest.mock.patch.object(cli, "_out", out.append), unittest.mock.patch.object(cli, "load_config", return_value=self.cfg):
            cli.main(["memoire", "outil envoi"])
            self.assertIn(f"#{bad}", out[0]); self.assertNotIn("Diagnostic", "".join(out))
            cli.main(["retenir", "envoi email", "send_email envoie l'email automatiquement après validation."])
            cli.main(["oublier", str(bad), "999"])
        db = DB(self.cfg.db_path)
        self.assertIsNone(db.one("SELECT 1 FROM memory WHERE id=?", (bad,)))
        self.db.remember("support", "envoi email", "ancienne note")
        self.assertEqual(db.recall("envoi email")[0]["agent"], "proprietaire")
        db.conn.close()
        self.assertTrue(any("introuvable" in x for x in out))


class StartupTest(unittest.TestCase):
    def test_startup_file_on_windows(self):
        from ai_studio import __main__ as cli
        d = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, d, True)
        with unittest.mock.patch.dict(os.environ, {"APPDATA": str(d), "AI_STUDIO_HOME": str(d)}), \
             unittest.mock.patch.object(cli.sys, "platform", "win32"), unittest.mock.patch.object(cli, "_out"):
            cli.main(["demarrage-auto"])
            f = cli.startup_file()
            self.assertIn("demarrer.bat\" --auto", f.read_text(encoding="utf-8"))
            cli.main(["demarrage-auto", "--retirer"])
            self.assertFalse(f.exists())


if __name__ == "__main__":
    unittest.main()
