"""Remise à zéro opérationnelle (sauvegarde d'abord, conservation de ce qui a de la valeur)."""
import unittest

from ai_studio import reset
from test_core import Base


class ResetTest(Base):
    def test_fresh_start_keeps_what_matters(self):
        db = self.db
        oid = db.add_objective("Vieille mission"); t = db.add_task("vieille tâche", "pdg", objective_id=oid)
        db.add_approval("ventes", t, "send_email", {"to": "a@b.fr", "subject": "s", "body": "b"}, "r")
        db.add_cost("pdg", t, "m", 1, 1, 0, 0.2)
        db.run("INSERT INTO sales(id, created_at, amount, currency, livemode, recorded_at) VALUES ('essai_1','2026-11-27T10:00:00+00:00',89,'EUR',0,'x')")
        db.run("INSERT INTO sales(id, created_at, amount, currency, livemode, recorded_at) VALUES ('cs_reel','2026-11-27T10:00:00+00:00',89,'EUR',1,'x')")
        db.run("INSERT INTO memory(agent, topic, content, created_at) VALUES ('proprietaire','consigne','tutoyer','x'), ('ventes','faux','pas de canal email','x')")
        (self.cfg.workspace / "livraisons").mkdir(parents=True, exist_ok=True)
        (self.cfg.workspace / "livraisons" / "rapport.md").write_text("ancien", encoding="utf-8")
        r = reset.fresh_start(self.cfg, db, agent_memory=True)
        for table in ("tasks", "objectives", "approvals", "chat"):
            self.assertEqual(db.one(f"SELECT COUNT(*) FROM {table}")[0], 0, table)
        self.assertEqual([x["id"] for x in db.q("SELECT id FROM sales")], ["cs_reel"])          # vraie vente conservée
        self.assertEqual(db.one("SELECT COUNT(*) FROM costs")[0], 1)                             # coûts conservés (budget)
        self.assertEqual([x["agent"] for x in db.q("SELECT agent FROM memory")], ["proprietaire"])
        self.assertEqual(list(self.cfg.workspace.iterdir()), [])
        from pathlib import Path
        self.assertTrue((Path(r["workspace_archive"]) / "livraisons" / "rapport.md").exists())
        self.assertTrue((Path(r["backup"]) / "studio.db").exists())
        # la nouvelle tâche ne récupère pas les coûts de l'ancienne (numérotation non réutilisée)
        self.assertGreater(db.add_task("nouvelle", "pdg"), t)
        self.assertEqual(db.add_objective("Nouvelle mission"), 1)                                  # les missions repartent à 1


    def test_test_prospects_cleared_stop_always_kept(self):
        from ai_studio import prospects
        self.cfg.data["company"]["test_mode"] = {"enabled": True, "recipients": ["Moi@Exemple.fr"]}
        for e, st in (("moi@exemple.fr", "contacte"), ("autre@societe.fr", "contacte"), ("zoe@cafe.fr", "stop")):
            prospects.save(self.db, e, "https://x.fr"); prospects.set_status(self.db, e, st)
        self.assertEqual(reset.fresh_start(self.cfg, self.db)["prospects"], 1)
        self.assertEqual(sorted(r[0] for r in self.db.q("SELECT email FROM prospects")), ["autre@societe.fr", "zoe@cafe.fr"])
        self.assertEqual(reset.fresh_start(self.cfg, self.db, all_prospects=True)["prospects"], 1)
        self.assertEqual([r[0] for r in self.db.q("SELECT email FROM prospects")], ["zoe@cafe.fr"])     # « STOP » gardé


if __name__ == "__main__":
    unittest.main()
