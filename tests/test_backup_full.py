"""Sauvegardes complètes et vérifiées (audit OPS-01) : base, livrables, coffre des pièces validées, copie externe."""
import json
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path

from ai_studio import backup
from ai_studio.db import DB, iso
from ai_studio.outgoing import freeze, frozen_path
from test_core import Base


class FullBackupTest(Base):
    def setUp(self):
        super().setUp()
        self.cfg.ensure_dirs()
        (self.cfg.workspace / "livraisons").mkdir(parents=True, exist_ok=True)
        self.report = self.cfg.workspace / "livraisons" / "rapport.md"
        self.report.write_text("Rapport V1", encoding="utf-8")
        self.frozen = freeze(self.cfg, "livraisons/rapport.md")            # pièce jointe validée (coffre)
        self.db.add_objective("Objectif d'origine")

    def test_backup_contains_everything_with_manifest(self):
        d = backup.make_backup(self.cfg, self.db, "sauv")
        m = json.loads((d / "manifeste.json").read_text(encoding="utf-8"))
        self.assertTrue({"studio.db", "livrables.zip", "pieces_validees.zip"} <= set(m["files"]))
        with zipfile.ZipFile(d / "pieces_validees.zip") as z:
            self.assertTrue(any(n.endswith("rapport.md") for n in z.namelist()))
        self.assertEqual(backup.verify(self.cfg, "sauv"), [])
        self.assertFalse((d.parent / "sauv.en-cours").exists())

    def test_full_restore_brings_back_db_files_and_vault_together(self):
        backup.make_backup(self.cfg, self.db, "sauv")
        self.db.add_objective("Ajouté après")
        self.report.write_text("Rapport V2 modifié après", encoding="utf-8")
        (self.cfg.workspace / "livraisons" / "nouveau.md").write_text("créé après", encoding="utf-8")
        self.db.conn.close()
        r = backup.restore(self.cfg, "sauv")
        self.db = DB(self.cfg.db_path)
        self.assertEqual([x[0] for x in self.db.q("SELECT text FROM objectives")], ["Objectif d'origine"])
        self.assertEqual(self.report.read_text(encoding="utf-8"), "Rapport V1")          # livrables cohérents avec la base
        self.assertFalse((self.cfg.workspace / "livraisons" / "nouveau.md").exists())
        self.assertTrue(frozen_path(self.cfg, self.frozen).is_file())                    # coffre restauré et vérifié
        prev = Path(r["previous"])
        self.assertEqual((prev / "workspace" / "livraisons" / "rapport.md").read_text(encoding="utf-8"), "Rapport V2 modifié après")
        self.assertTrue((prev / "studio.db").is_file())                                  # rien n'est effacé

    def test_corrupted_backup_is_refused_before_touching_anything(self):
        d = backup.make_backup(self.cfg, self.db, "sauv")
        with open(d / "livrables.zip", "r+b") as f:
            f.seek(30); f.write(b"XXXX")
        self.assertTrue(any("livrables.zip" in p for p in backup.verify(self.cfg, "sauv")))
        self.db.add_objective("État actuel")
        with self.assertRaises(backup.BackupError):
            backup.restore(self.cfg, "sauv")
        self.assertEqual(len(self.db.q("SELECT 1 FROM objectives")), 2)                  # base actuelle intacte
        self.assertEqual(self.report.read_text(encoding="utf-8"), "Rapport V1")

    def test_old_backup_without_manifest_is_not_restored_blindly(self):
        old = backup.backup_dir(self.cfg) / "ancienne"
        old.mkdir(parents=True)
        shutil.copy2(self.cfg.db_path, old / "studio.db")
        self.assertIn("manifeste", backup.verify(self.cfg, "ancienne")[0])
        with self.assertRaises(backup.BackupError):
            backup.restore(self.cfg, "ancienne")

    def test_restore_refused_while_engine_runs(self):
        backup.make_backup(self.cfg, self.db, "sauv")
        self.db.kv_set("last_cycle_at", iso())
        with self.assertRaises(backup.BackupError) as e:
            backup.restore(self.cfg, "sauv")
        self.assertIn("moteur", str(e.exception))

    def test_external_copy_verified_and_pruned(self):
        ext = self.dir / "OneDrive"
        self.cfg.data["backup"].update(external_dir=str(ext), keep=2)
        for day in ("2026-11-26", "2026-11-27", "2026-11-28"):
            self.db.kv_set("last_backup_day", "")
            d = backup.make_backup(self.cfg, self.db, day)
            backup.copy_external(self.cfg, d)
        copies = sorted(p.name for p in (ext / "AI-Enterprise-Studio").iterdir())
        self.assertEqual(copies, ["2026-11-27", "2026-11-28"])
        self.assertEqual(backup.verify_dir(ext / "AI-Enterprise-Studio" / "2026-11-28"), [])

    def test_daily_backup_alerts_when_external_copy_fails(self):
        blocker = self.dir / "pas-un-dossier"
        blocker.write_text("x")
        self.cfg.data["backup"]["external_dir"] = str(blocker)
        notes = []
        import unittest.mock
        from ai_studio import notify
        with unittest.mock.patch.object(notify, "send", lambda c, d, t, m: notes.append(m) or True):
            self.assertIsNotNone(backup.daily(self.cfg, self.db))                         # la sauvegarde locale est faite
        self.assertTrue(any("Copie externe" in n for n in notes))

    def test_prune_removes_read_only_vault_copies(self):
        self.cfg.data["backup"]["keep"] = 1
        backup.make_backup(self.cfg, self.db, "a")
        self.db.conn.close()
        backup.restore(self.cfg, "a")                                                    # coffre restauré en lecture seule
        self.db = DB(self.cfg.db_path)
        backup.make_backup(self.cfg, self.db, "b")
        self.assertEqual([b["name"] for b in backup.list_backups(self.cfg)], ["b"])
