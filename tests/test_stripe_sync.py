"""Synchronisation Stripe fiable (audit FIN-02) : aucun paiement ne doit être perdu (Stripe simulé).

Cas couverts : session ouverte puis payée après un passage, sessions créées à la même seconde, paiement différé
(prélèvement), pagination incomplète, arrêt du programme entre l'enregistrement de la vente et l'ouverture de la
commande, redémarrage de la base.
"""
import json
import os
import unittest.mock
from datetime import timedelta

from ai_studio import commerce
from ai_studio.db import DB, now
from test_commerce import Base, session


class FakeStripe:
    """Liste des sessions (la plus récente d'abord, comme Stripe) et lecture d'une session par son identifiant."""
    def __init__(self, *sessions):
        self.sessions = {s["id"]: s for s in sessions}
        self.calls = []

    def pay(self, sid):
        self.sessions[sid].update(payment_status="paid", status="complete")

    def __call__(self, method, path, params):
        self.calls.append((path, dict(params)))
        if path.startswith("checkout/sessions/"):
            return self.sessions[path.rsplit("/", 1)[1]]
        rows = sorted((s for s in self.sessions.values() if s["created"] >= params.get("created[gte]", 0)
                       and s["created"] > params.get("created[gt]", -1)),
                      key=lambda s: (-s["created"], s["id"]))
        if params.get("starting_after"):
            rows = rows[[r["id"] for r in rows].index(params["starting_after"]) + 1:]
        return {"data": rows[:params["limit"]], "has_more": len(rows) > params["limit"]}


def open_session(sid, created, **kw):
    s = session(sid, created, paid=False, **kw)
    s["status"] = "open"
    return s


class StripeSyncTest(Base):
    def setUp(self):
        super().setUp()
        os.environ["STRIPE_SECRET_KEY"] = "rk_test"
        self.t0 = int(now().timestamp())
        self.db.kv_set("stripe_sync_ts", str(self.t0 - 600))    # pas de premier passage « historique »

    def orders(self):
        return self.db.q("SELECT id, stage, task_id FROM sales ORDER BY id")

    def pm_tasks(self):
        return self.db.one("SELECT COUNT(*) FROM tasks WHERE agent='chef_projet'")[0]

    def test_session_paid_after_a_pass_is_not_lost(self):
        stripe = FakeStripe(open_session("cs_lent", self.t0 - 120))     # le client a ouvert le lien, il tape sa carte
        self.assertEqual(commerce.sync_stripe(self.cfg, self.db, stripe)["new"], 0)
        stripe.pay("cs_lent")                                              # il paie après le passage
        r = commerce.sync_stripe(self.cfg, self.db, stripe)
        self.assertEqual((r["new"], r["opened"]), (1, 1))
        self.assertEqual(self.orders()[0]["stage"], "a_livrer")
        self.assertEqual(self.pm_tasks(), 1)
        self.assertEqual(len(self.toasts), 1)

    def test_same_second_sessions_are_all_seen(self):
        stripe = FakeStripe(session("cs_a", self.t0 - 60))
        commerce.sync_stripe(self.cfg, self.db, stripe)
        stripe.sessions["cs_b"] = session("cs_b", self.t0 - 60)          # même seconde, visible seulement au passage suivant
        self.assertEqual(commerce.sync_stripe(self.cfg, self.db, stripe)["new"], 1)
        self.assertEqual([r["id"] for r in self.orders()], ["cs_a", "cs_b"])
        self.assertEqual(self.pm_tasks(), 2)

    def test_delayed_payment_outside_window_is_followed(self):
        old = self.t0 - 3 * 86400
        s = session("cs_sepa", old, paid=False); s["status"] = "complete"   # prélèvement SEPA : payé plusieurs jours après
        self.db.kv_set("stripe_sync_ts", str(old))
        stripe = FakeStripe(s)
        commerce.sync_stripe(self.cfg, self.db, stripe)
        self.assertIn("cs_sepa", json.loads(self.db.kv_get("stripe_async_pending")))
        self.assertEqual(self.orders(), [])
        stripe.pay("cs_sepa")
        r = commerce.sync_stripe(self.cfg, self.db, stripe)                  # hors de la fenêtre : lue par son identifiant
        self.assertEqual(r["new"], 1)
        self.assertIn(("checkout/sessions/cs_sepa", {"expand[]": "line_items"}), stripe.calls)
        self.assertEqual(json.loads(self.db.kv_get("stripe_async_pending")), {})
        self.assertEqual(self.orders()[0]["stage"], "a_livrer")

    def test_delayed_payment_abandoned_after_14_days(self):
        self.db.kv_set("stripe_async_pending", json.dumps({"cs_vieux": self.t0 - 15 * 86400}))
        stripe = FakeStripe()
        commerce.sync_stripe(self.cfg, self.db, stripe)
        self.assertEqual(json.loads(self.db.kv_get("stripe_async_pending")), {})
        self.assertFalse(any(p.startswith("checkout/sessions/") for p, _ in stripe.calls))

    def test_no_silent_cap_and_cursor_kept_when_incomplete(self):
        stripe = FakeStripe(*[session(f"cs_{i:03d}", self.t0 - 300 + i) for i in range(250)])
        with unittest.mock.patch.object(commerce, "STRIPE_MAX_PAGES", 2):
            r = commerce.sync_stripe(self.cfg, self.db, stripe)
        self.assertFalse(r["complete"])
        self.assertEqual(self.db.kv_get("stripe_sync_ts"), str(self.t0 - 600))   # le curseur n'avance pas
        r = commerce.sync_stripe(self.cfg, self.db, stripe)                       # passage suivant complet
        self.assertTrue(r["complete"])
        self.assertEqual(len(self.orders()), 250)
        self.assertEqual(self.pm_tasks(), 250)

    def test_crash_between_sale_and_order_is_recovered_after_restart(self):
        stripe = FakeStripe(session("cs_crash", self.t0 - 60))
        with unittest.mock.patch.object(commerce, "open_order", side_effect=RuntimeError("coupure")):
            commerce.sync_stripe(self.cfg, self.db, stripe)
        self.assertEqual(self.orders()[0]["stage"], "nouvelle")                   # vente enregistrée, commande pas encore ouverte
        self.assertEqual(self.toasts, [])
        self.db.conn.close()
        self.db = DB(self.cfg.db_path)                                            # redémarrage : migrations rejouées
        self.addCleanup(self.db.conn.close)
        self.assertEqual(self.orders()[0]["stage"], "nouvelle")                   # pas enterrée en « historique »
        r = commerce.sync_stripe(self.cfg, self.db, stripe)
        self.assertEqual((r["new"], r["opened"]), (0, 1))
        self.assertEqual(self.orders()[0]["stage"], "a_livrer")
        self.assertEqual(len(self.toasts), 1)

    def test_interrupted_order_opening_creates_no_duplicate_task(self):
        commerce.sync_stripe(self.cfg, self.db, FakeStripe(session("cs_x", self.t0 - 60)))
        self.assertEqual(self.pm_tasks(), 1)
        self.db.run("UPDATE sales SET task_id=NULL WHERE id='cs_x'")             # arrêt juste avant l'enregistrement de la tâche
        commerce.sync_stripe(self.cfg, self.db, FakeStripe(session("cs_x", self.t0 - 60)))
        self.assertEqual(self.pm_tasks(), 1)
        self.assertIsNotNone(self.orders()[0]["task_id"])

    def test_paid_session_recorded_once_across_overlapping_passes(self):
        stripe = FakeStripe(session("cs_1", self.t0 - 60))
        for _ in range(3):
            commerce.sync_stripe(self.cfg, self.db, stripe)
        self.assertEqual(len(self.orders()), 1)
        self.assertEqual(self.pm_tasks(), 1)
        self.assertEqual(len(self.toasts), 1)

    def test_window_covers_downtime(self):
        self.db.kv_set("stripe_sync_ts", str(self.t0 - 5 * 86400))               # PC éteint 5 jours
        stripe = FakeStripe(session("cs_pendant", self.t0 - 4 * 86400))
        commerce.sync_stripe(self.cfg, self.db, stripe)
        self.assertEqual(len(self.orders()), 1)
        start = stripe.calls[0][1]["created[gte]"]
        self.assertLessEqual(start, self.t0 - 5 * 86400 - 26 * 3600 + 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
