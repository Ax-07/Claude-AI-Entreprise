"""Catalogue d'offres et suivi des commandes jusqu'à la livraison (Stripe et SMTP simulés)."""
import os
import sqlite3
import unittest
import unittest.mock
from datetime import timedelta

from ai_studio import approvals, commerce
from ai_studio.db import DB, iso, now
from ai_studio.tools import ToolContext, run_tool
from test_commerce import Base, session


class OrdersTest(Base):
    def setUp(self):
        super().setUp()
        os.environ["STRIPE_SECRET_KEY"] = "rk_test"
        self.db.kv_set("stripe_sync_ts", "0")       # pas de premier passage « historique »

    def ctx(self, agent="support"):
        from ai_studio.agents import ROLES
        tid = self.db.add_task("t", agent)
        return ToolContext(self.cfg, self.db, agent, self.db.task(tid), list(ROLES))

    def offer(self, **kw):
        args = {"name": "Diagnostic SEO local", "price_eur": 250, "summary": "Audit de la page d'accueil",
                "deliverable": "Rapport PDF priorisé", "delay_hours": 48, "required_info": "URL du site, ville",
                "producer": "recherche", **kw}
        return run_tool("save_offer", self.ctx("pdg"), args)

    def sell(self, sid="cs_1", product="Diagnostic SEO local", link="plink_x", email="client@exemple.fr", live=True):
        s = session(sid, int(now().timestamp()), product=product, email=email, live=live)
        s["payment_link"] = link
        commerce.sync_stripe(self.cfg, self.db, lambda m, p, params: {"data": [s], "has_more": False})
        return commerce.order_number(self.db, sid)

    def test_offer_catalog_and_payment_link(self):
        self.assertIn("ajoutée au catalogue", self.offer())
        self.assertIn("mise à jour", self.offer(price_eur=290))
        self.assertIn("Agent inconnu", self.offer(producer="stagiaire"))
        out = run_tool("propose_payment_link", self.ctx("ventes"), {"offer": "diagnostic seo local", "reason": "vendre"})
        self.assertIn("créé automatiquement", out)
        aid = self.db.pending_approvals()[0]["id"]
        calls = iter([{"id": "price_1"}, {"id": "plink_9", "url": "https://buy.stripe.com/test_9"}])
        with unittest.mock.patch.object(approvals, "stripe_request", lambda *a: next(calls)):
            approvals.decide(self.cfg, self.db, aid, True)
        o = self.db.one("SELECT * FROM offers")
        self.assertEqual((o["price_eur"], o["payment_link_id"]), (290, "plink_9"))
        self.assertIn("buy.stripe.com/test_9", run_tool("list_offers", self.ctx("support"), {}))

    def test_sale_matched_to_offer_gets_deadline_and_clear_brief(self):
        self.offer()
        self.db.run("UPDATE offers SET payment_link_id='plink_x'")
        n = self.sell(product="Autre nom affiché")          # retrouvée par le lien de paiement
        s = commerce.get_order(self.db, n)
        self.assertEqual(s["stage"], "a_livrer")
        self.assertIsNotNone(s["offer_id"])
        self.assertAlmostEqual((commerce.datetime.fromisoformat(s["due_at"]) - commerce.datetime.fromisoformat(s["created_at"])).total_seconds(), 48 * 3600)
        t = self.db.one("SELECT * FROM tasks WHERE order_ref=?", (n,))
        self.assertEqual(t["agent"], "pdg")
        for expected in ("Rapport PDF priorisé", "l'agent recherche", f"deliver_order, order={n}", "n'est PAS un livrable",
                         "jamais « aucun délai confirmé »"):
            self.assertIn(expected, t["description"])

    def test_unknown_product_asks_to_create_offer(self):
        n = self.sell(product="Test diagnostic", live=False)
        t = self.db.one("SELECT description FROM tasks WHERE order_ref=?", (n,))[0]
        self.assertIn("Aucune fiche d'offre", t)
        self.assertIn("mode test", t)          # vente test : à traiter comme une vraie

    def test_delegation_keeps_order_and_watchdog_relaunches(self):
        n = self.sell()
        pdg = self.db.one("SELECT * FROM tasks WHERE order_ref=?", (n,))
        ctx = ToolContext(self.cfg, self.db, "pdg", pdg, ["pdg", "recherche", "support"])
        run_tool("delegate_task", ctx, {"agent": "recherche", "title": "Produire le rapport", "description": "…"})
        child = self.db.one("SELECT * FROM tasks WHERE agent='recherche'")
        self.assertEqual(child["order_ref"], n)
        self.assertEqual(commerce.follow_orders(self.cfg, self.db)["nudged"], [])    # du travail en cours
        self.db.run("UPDATE tasks SET status='done', updated_at=?", (iso(now() - timedelta(hours=2)),))
        out = commerce.follow_orders(self.cfg, self.db)
        self.assertEqual(out["nudged"], [n])
        relance = self.db.one("SELECT * FROM tasks WHERE title LIKE ?", (f"Commande n°{n} : relance 1%",))
        self.assertIn("RELANCE AUTOMATIQUE", relance["description"])
        self.assertEqual(commerce.follow_orders(self.cfg, self.db)["nudged"], [])    # la relance est en cours

    def test_pending_delivery_approval_counts_as_work(self):
        n = self.sell()
        self.db.run("UPDATE tasks SET status='done', updated_at=?", (iso(now() - timedelta(hours=2)),))
        self.db.add_approval("support", None, "send_email", {"to": "c@x.fr", "subject": "s", "body": "b", "order": n}, "r")
        self.assertEqual(commerce.follow_orders(self.cfg, self.db)["nudged"], [])

    def test_max_nudges_then_owner_notified_once(self):
        n = self.sell()
        self.db.run("UPDATE tasks SET status='done'")
        self.db.run("UPDATE sales SET nudges=3, last_nudge_at=?", (iso(now() - timedelta(hours=2)),))
        self.assertEqual(commerce.follow_orders(self.cfg, self.db)["stuck"], [n])
        self.assertEqual(commerce.follow_orders(self.cfg, self.db)["stuck"], [])
        self.assertTrue(any("bloquée" in x for x in self.toasts))

    def test_late_order_notified(self):
        n = self.sell()
        self.db.run("UPDATE sales SET due_at=?", (iso(now() - timedelta(hours=1)),))
        self.assertEqual(commerce.follow_orders(self.cfg, self.db)["late"], [n])
        self.assertTrue(any("en retard" in x for x in self.toasts))

    def test_waiting_for_client_not_nudged_before_delay(self):
        n = self.sell()
        self.db.run("UPDATE tasks SET status='done'")
        self.assertIn("Refusé", run_tool("set_order_status", self.ctx("support"), {"order": n, "status": "attente_client"}))
        aid = self.db.add_approval("support", None, "send_email", {"to": "client@exemple.fr", "subject": "Votre URL ?", "body": "b"}, "r")
        self.db.set_approval(aid, status="executed", decided_at=iso())
        self.assertIn("attente_client", run_tool("set_order_status", self.ctx("support"), {"order": n, "status": "attente_client", "note": "URL demandée"}))
        self.assertEqual(commerce.follow_orders(self.cfg, self.db)["nudged"], [])
        self.db.run("UPDATE sales SET last_nudge_at=?", (iso(now() - timedelta(hours=49)),))
        self.assertEqual(commerce.follow_orders(self.cfg, self.db)["nudged"], [n])
        self.assertIn("n'a pas répondu", self.db.q("SELECT description FROM tasks ORDER BY id DESC LIMIT 1")[0][0])

    def test_client_reply_resumes_order(self):
        from test_commerce import FakeIMAP, mail
        os.environ.update(SMTP_HOST="smtp.gmail.com", SMTP_USER="studio@gmail.com", SMTP_PASSWORD="x")
        n = self.sell(email="marie@cafe.fr")
        self.db.run("UPDATE sales SET stage='attente_client', nudges=2")
        self.db.kv_set("inbox_last_uid", "0"); self.db.kv_set("inbox_uidvalidity", "7")
        commerce.sync_inbox(self.cfg, self.db, connect=lambda: FakeIMAP({1: mail("Marie <marie@cafe.fr>", "Re: infos", "Voici l'URL", "<m@x>")}))
        s = commerce.get_order(self.db, n)
        self.assertEqual((s["stage"], s["nudges"]), ("a_livrer", 0))
        t = self.db.one("SELECT * FROM tasks WHERE title LIKE 'Répondre à Marie%'")
        self.assertEqual((t["order_ref"], t["priority"]), (n, 1))

    def test_deliver_order_flow(self):
        n = self.sell()
        d = self.cfg.workspace / "livraisons" / f"commande-{n}"
        d.mkdir(parents=True)
        (d / "plan.md").write_text("plan", encoding="utf-8")
        (d / "rapport.md").write_text("# Rapport\n" + "constat détaillé. " * 60, encoding="utf-8")
        args = {"order": n, "subject": "Votre diagnostic", "body": "Bonjour, voici votre rapport."}
        self.assertIn("trop court", run_tool("deliver_order", self.ctx(), {**args, "attachment": f"livraisons/commande-{n}/plan.md"}))
        self.assertIn("introuvable", run_tool("deliver_order", self.ctx(), {**args, "attachment": "livraisons/x.md"}))
        self.assertIn(f"livraisons/commande-{n}/rapport.md", run_tool("list_orders", self.ctx("pdg"), {}))
        out = run_tool("deliver_order", self.ctx(), {**args, "attachment": f"livraisons/commande-{n}/rapport.md"})
        self.assertIn("marquée livrée", out)
        self.assertIn("attend déjà", run_tool("deliver_order", self.ctx(), {**args, "attachment": f"livraisons/commande-{n}/rapport.md"}))
        aid = self.db.pending_approvals()[0]["id"]
        self.assertEqual(commerce.follow_orders(self.cfg, self.db)["nudged"], [])
        os.environ.update(SMTP_HOST="h", SMTP_USER="u", SMTP_PASSWORD="p")
        tasks_before = self.db.one("SELECT COUNT(*) FROM tasks")[0]
        with unittest.mock.patch.dict(approvals.EXECUTORS, {"send_email": lambda c, p: "Email envoyé"}):
            approvals.decide(self.cfg, self.db, aid, True)
        self.assertEqual(commerce.get_order(self.db, n)["stage"], "livree")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM tasks")[0], tasks_before)    # pas de tâche de suite inutile
        self.assertEqual(run_tool("list_orders", self.ctx("pdg"), {}), "Aucune commande dans cet état.")

    def test_refusal_note_sticks_to_order_and_resumes_it(self):
        n = self.sell()
        pdg = self.db.one("SELECT * FROM tasks WHERE order_ref=?", (n,))
        self.db.run("UPDATE sales SET stage='attente_client', nudges=2")
        aid = self.db.add_approval("support", pdg["id"], "send_email", {"to": "client@exemple.fr", "subject": "Re: ?", "body": "b"}, "r")
        approvals.decide(self.cfg, self.db, aid, False, "Pas besoin de redemander : page d'accueil de ax-07.fr, annonce 48 h.")
        s = commerce.get_order(self.db, n)
        self.assertEqual((s["stage"], s["nudges"]), ("a_livrer", 0))
        self.assertIn("page d'accueil de ax-07.fr", s["owner_notes"])
        follow = self.db.q("SELECT description FROM tasks ORDER BY id DESC LIMIT 1")[0][0]
        self.assertIn("CONSIGNES DU PROPRIÉTAIRE", follow)
        self.db.run("UPDATE tasks SET status='done', updated_at=?", (iso(now() - timedelta(hours=2)),))
        commerce.follow_orders(self.cfg, self.db)
        relance = self.db.q("SELECT description FROM tasks ORDER BY id DESC LIMIT 1")[0][0]
        self.assertIn("font foi", relance)
        self.assertIn("ax-07.fr", relance)
        self.assertIn("CONSIGNES DU PROPRIÉTAIRE", run_tool("list_orders", self.ctx("pdg"), {}))

    def test_cli_consigne(self):
        from ai_studio import __main__ as cli
        n = self.sell()
        out = []
        with unittest.mock.patch.object(cli, "_out", out.append), unittest.mock.patch.object(cli, "load_config", return_value=self.cfg):
            cli.main(["consigne", str(n), "Livre le diagnostic de la page d'accueil, hypothèses signalées."])
        self.assertIn("le PDG s'en occupe", out[0])
        db = DB(self.cfg.db_path)
        t = db.one("SELECT * FROM tasks ORDER BY id DESC LIMIT 1")
        self.assertEqual((t["agent"], t["order_ref"], t["priority"]), ("pdg", n, 1))
        self.assertIn("hypothèses signalées", t["description"])
        db.conn.close()


class AtelierTest(Base):
    def test_role_limits_and_default_producer(self):
        from ai_studio.agents import ROLES
        from ai_studio.llm import BudgetExceeded, BudgetGuard
        a = ROLES["atelier"]
        self.assertEqual((a.model, a.max_steps), ("strong", 16))
        self.assertIn("deliver_order", a.mission)
        g = BudgetGuard(self.cfg, self.db)
        t1 = self.db.add_task("rapport", "atelier"); t2 = self.db.add_task("étude", "recherche")
        for t in (t1, t2):
            self.db.add_cost("x", t, "m", 1, 1, 0, 0.20)
        g.check(t1)                                   # 0,20 $ < plafond de l'atelier (0,40 $)
        with self.assertRaises(BudgetExceeded):
            g.check(t2)                               # plafond général (0,15 $)
        from ai_studio.agents import ROLES as R
        ctx = ToolContext(self.cfg, self.db, "pdg", None, list(R))
        run_tool("save_offer", ctx, {"name": "Audit", "price_eur": 90, "summary": "s", "deliverable": "d", "delay_hours": 24})
        self.assertEqual(self.db.one("SELECT producer FROM offers")[0], "atelier")


class LegalTest(Base):
    def test_review_queued_for_pages_and_links_not_replies(self):
        from ai_studio import legal
        from ai_studio.agents import ROLES
        page = self.db.add_approval("marketing", None, "publish_page", {"path": "o.html", "url": "u", "title": "Offre", "file": "site/o.html", "html": "x"}, "r")
        reply = self.db.add_approval("support", None, "send_email", {"to": "a@b.fr", "subject": "Re", "body": "b", "in_reply_to": 1}, "r")
        prospect = self.db.add_approval("ventes", None, "send_email", {"to": "c@d.fr", "subject": "Bonjour", "body": "Offre"}, "r")
        created = legal.queue_reviews(self.cfg, self.db)
        self.assertEqual(len(created), 1)
        t = self.db.task(created[0])
        self.assertEqual(t["agent"], "juridique")
        self.assertIn(f"approval_id={page}", t["description"])
        self.assertEqual(legal.queue_reviews(self.cfg, self.db), [])            # une seule fois
        self.assertEqual(self.db.approval(reply)["review_task"], 0)
        self.cfg.data["legal_review"]["prospection_emails"] = True
        self.db.run("UPDATE approvals SET review_task=NULL WHERE id IN (?,?)", (reply, prospect))
        self.assertEqual(len(legal.queue_reviews(self.cfg, self.db)), 1)        # la prospection, pas la réponse
        ctx = ToolContext(self.cfg, self.db, "juridique", t, list(ROLES))
        self.assertIn("Verdict possible", run_tool("legal_review", ctx, {"approval_id": page, "verdict": "bof", "notes": "?"}))
        run_tool("legal_review", ctx, {"approval_id": page, "verdict": "corrections", "notes": "Ajouter le prix TTC."})
        self.assertIn("⚠️ À corriger — Ajouter le prix TTC.", self.db.approval(page)["review"])
        self.db.set_approval(page, status="rejected")
        self.assertIn("déjà décidé", run_tool("legal_review", ctx, {"approval_id": page, "verdict": "ok", "notes": "x"}))

    def test_divisions_cover_every_agent_and_shape_the_prompt(self):
        from ai_studio.agents import DIVISIONS, ROLES, system_prompt
        names = [n for ns in DIVISIONS.values() for n in ns]
        self.assertEqual(sorted(names), sorted(ROLES))
        prompt = system_prompt(ROLES["pdg"], self.cfg)
        self.assertIn("Division production :\n  - atelier", prompt)

    def test_role(self):
        from ai_studio.agents import ROLES
        r = ROLES["juridique"]
        self.assertEqual(r.model, "strong")
        self.assertIn("legal_review", r.all_tools())
        self.assertIn("legifrance", r.mission)


class MigrationTest(Base):
    def test_old_database_is_upgraded_without_loss(self):
        path = self.dir / "ancienne.db"
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
              agent TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 3, parent_id INTEGER,
              objective_id INTEGER, created_by TEXT NOT NULL DEFAULT 'owner', depth INTEGER NOT NULL DEFAULT 0,
              attempts INTEGER NOT NULL DEFAULT 0, result TEXT, cost_usd REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL);
            CREATE TABLE sales (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL,
              customer_email TEXT, customer_name TEXT, product TEXT, payment_link TEXT, livemode INTEGER NOT NULL DEFAULT 1,
              task_id INTEGER, recorded_at TEXT NOT NULL);
            INSERT INTO tasks(id, title, agent, created_at, updated_at) VALUES (18, 'Livrer', 'pdg', 't', 't');
            INSERT INTO sales VALUES ('cs_a', '2026-11-27T13:18:00+00:00', 10, 'EUR', 'x@y.fr', 'X', 'Test diagnostic', NULL, 0, 18, 't');
            INSERT INTO sales VALUES ('cs_b', '2026-11-01T10:00:00+00:00', 5, 'EUR', NULL, NULL, NULL, NULL, 0, NULL, 't');
        """)
        con.commit(); con.close()
        db = DB(path)
        self.assertEqual([r["stage"] for r in db.q("SELECT stage FROM sales ORDER BY rowid")], ["a_livrer", "historique"])
        self.assertEqual(db.one("SELECT order_ref FROM tasks WHERE id=18")[0], 1)
        self.assertIn("première tâche de livraison : #18", commerce.delivery_brief(db, 1))
        db.conn.close()
        DB(path).conn.close()            # migration idempotente


if __name__ == "__main__":
    unittest.main()
