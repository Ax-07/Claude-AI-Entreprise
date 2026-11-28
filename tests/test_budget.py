"""Plafonds de dépense stricts (audit FIN-01) : réservation du coût maximal avant chaque appel (faux modèle, aucun coût).

Le faux modèle « gourmand » consomme toujours le maximum autorisé (entrée estimée + max_tokens) : c'est le pire cas.
Limite connue : le plafond est strict tant que l'entrée réelle ne dépasse pas l'estimation (1 token pour 3 caractères,
prudent : un texte français compte plutôt 3,5 à 4 caractères par token).
"""
import threading
import unittest

from ai_studio.db import DB, iso, now
from ai_studio.llm import BudgetExceeded, BudgetGuard, MIN_OUTPUT_TOKENS, compute_cost, estimate_input_tokens
from ai_studio.runner import run_task
from test_core import Base, FakeLLM, tu

MODEL = "claude-sonnet-5-5"          # 2 $ / 10 $ par million : 6 000 tokens de sortie = 0,06 $


class GreedyLLM:
    """Pire cas : la réponse utilise tout ce qui est autorisé."""
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def complete(self, model, system, messages, tools=None, max_tokens=2000):
        self.calls.append(max_tokens)
        if self.fail:
            raise RuntimeError("coupure réseau")
        return {"content": [{"type": "text", "text": "fin"}], "stop_reason": "end_turn",
                "usage": {"input_tokens": estimate_input_tokens(system, messages, tools), "output_tokens": max_tokens}}


class BudgetTest(Base):
    def setUp(self):
        super().setUp()
        self.cfg.data["budget"].update(daily_usd=0.65, monthly_usd=20.0, per_task_usd=5.0)
        self.guard = BudgetGuard(self.cfg, self.db)
        self.msgs = [{"role": "user", "content": "x" * 3000}]

    def spend(self, usd):
        self.db.add_cost("test", None, MODEL, 0, 0, 0, usd)

    def call(self, guard, llm, max_tokens=6000, task_id=None):
        rid, allowed = guard.reserve("pdg", task_id, MODEL, [], self.msgs, [], max_tokens)
        try:
            resp = llm.complete(MODEL, [], self.msgs, [], allowed)
        except BaseException:
            guard.release(rid)
            raise
        guard.settle(rid, "pdg", task_id, MODEL, resp["usage"])
        return allowed

    def test_audit_scenario_never_exceeds_daily_cap(self):
        self.spend(0.56)                                   # simulation de l'audit : 0,56 $ dépensés sur 0,65 $
        llm = GreedyLLM()
        while True:
            try:
                self.call(self.guard, llm)
            except BudgetExceeded as e:
                self.assertIn("Budget du jour", str(e))
                break
        self.assertLessEqual(self.db.spent("day"), 0.65 + 1e-9)
        self.assertTrue(all(n >= MIN_OUTPUT_TOKENS for n in llm.calls))
        self.assertEqual(llm.calls[0], 6000)
        self.assertTrue(any(n < 6000 for n in llm.calls[1:]))   # réponse raccourcie pour tenir dans le budget restant

    def test_refused_before_calling_the_model_when_nothing_left(self):
        self.spend(0.649)
        llm = GreedyLLM()
        with self.assertRaises(BudgetExceeded):
            self.call(self.guard, llm)
        self.assertEqual(llm.calls, [])                    # aucun appel payant

    def test_old_check_then_record_could_overshoot(self):
        """Ce que faisait l'ancien contrôle : vérifier ce qui est déjà dépensé, puis payer l'appel entier."""
        self.spend(0.56)
        self.assertLess(self.db.spent("day"), 0.65)       # l'ancien contrôle laissait passer…
        full, *_ = compute_cost(self.cfg["prices"], MODEL, {"input_tokens": 20000, "output_tokens": 6000})
        self.assertGreater(0.56 + full, 0.65)              # … un appel qui dépasse le plafond

    def test_reservations_of_other_connection_are_counted(self):
        other = DB(self.cfg.db_path)                       # discussion avec le PDG : autre connexion, même base
        self.addCleanup(other.conn.close)
        g2 = BudgetGuard(self.cfg, other)
        self.spend(0.55)                                   # reste 0,10 $
        r1, a1 = self.guard.reserve("atelier", None, MODEL, [], self.msgs, [], 6000)   # appel du moteur en cours
        r2, a2 = g2.reserve("pdg", None, MODEL, [], self.msgs, [], 6000)               # discussion en même temps
        self.assertEqual(a1, 6000)
        self.assertLess(a2, 6000)                          # la réservation du moteur est prise en compte
        held = self.db.one("SELECT SUM(usd) FROM budget_reservations")[0]
        self.assertLessEqual(0.55 + held, 0.65 + 1e-9)
        self.guard.release(r1); g2.release(r2)

    def test_concurrent_threads_never_exceed_cap(self):
        self.spend(0.40)
        errors, spent = [], []
        def worker():
            db = DB(self.cfg.db_path)
            g, llm = BudgetGuard(self.cfg, db), GreedyLLM()
            try:
                for _ in range(10):
                    try:
                        self.call(g, llm)
                    except BudgetExceeded:
                        break
            except Exception as e:                         # pragma: no cover
                errors.append(e)
            finally:
                db.conn.close()
        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])
        self.assertLessEqual(self.db.spent("day"), 0.65 + 1e-9)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM budget_reservations")[0], 0)

    def test_reservation_released_when_call_fails(self):
        with self.assertRaises(RuntimeError):
            self.call(self.guard, GreedyLLM(fail=True))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM budget_reservations")[0], 0)
        self.assertEqual(self.db.spent("day"), 0)

    def test_stale_reservation_from_a_crash_is_ignored(self):
        self.db.run("INSERT INTO budget_reservations(agent, task_id, model, usd, created_at) VALUES ('pdg', NULL, ?, 0.60, ?)",
                    (MODEL, iso(now().replace(year=now().year - 1))))
        self.call(self.guard, GreedyLLM())                 # pas bloqué par une réservation orpheline
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM budget_reservations")[0], 0)

    def test_web_searches_are_part_of_the_maximum(self):
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
        r1, _ = self.guard.reserve("recherche", None, MODEL, [], self.msgs, [], 6000)
        r2, _ = self.guard.reserve("recherche", None, MODEL, [], self.msgs, tools, 6000)
        a, b = (self.db.one("SELECT usd FROM budget_reservations WHERE id=?", (r,))[0] for r in (r1, r2))
        self.assertAlmostEqual(b - a, 3 * self.cfg["prices"]["web_search_per_call"], places=4)

    def test_runner_uses_reservation_and_stops_at_cap(self):
        self.cfg.data["models"] = {"anthropic": {"default": MODEL, "strong": MODEL}}
        self.spend(0.645)
        tid = self.db.add_task("Étude", "recherche", "x")
        llm = GreedyLLM()
        with self.assertRaises(BudgetExceeded):
            run_task(self.cfg, self.db, llm, BudgetGuard(self.cfg, self.db), self.db.task(tid))
        self.assertLessEqual(self.db.spent("day"), 0.65 + 1e-9)
        self.assertEqual(self.db.task(tid)["status"], "pending")         # reprise demain, sans perte
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM budget_reservations")[0], 0)


if __name__ == "__main__":
    unittest.main()
