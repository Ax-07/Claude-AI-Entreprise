"""Le cœur qui tourne 24 h/24 : revues d'objectifs par le PDG, exécution des tâches, respect des budgets."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import backup, commerce, notify
from .db import iso, now
from .llm import BudgetExceeded, BudgetGuard, LLMError
from .runner import run_task

REVIEW_TEMPLATE = (
    "Objectif #{oid} fixé par le propriétaire : « {text} »\n\n"
    "1. Consulte list_tasks, recall et les fichiers existants pour faire le point.\n"
    "2. Décide des prochaines actions les plus utiles (au maximum 3 nouvelles tâches), en évitant les doublons "
    "avec les tâches déjà en cours.\n"
    "3. Confie-les aux bons agents avec delegate_task (description précise + livrable attendu).\n"
    "4. Si l'objectif est atteint, utilise set_objective_status. Si une décision du propriétaire est nécessaire, "
    "utilise request_owner_decision.\n"
    "5. Termine avec complete_task : point d'avancement en 5 lignes maximum."
)


class Studio:
    def __init__(self, cfg, db, llm):
        self.cfg, self.db, self.llm = cfg, db, llm
        self.guard = BudgetGuard(cfg, db)
        self._paused_reason = None

    def recover(self) -> None:
        """Après un arrêt brutal, les tâches « running » repassent en attente."""
        n = self.db.run("UPDATE tasks SET status='pending' WHERE status='running'")
        self.db.log("systeme", "start", "Démarrage du moteur")

    def schedule_reviews(self) -> int:
        created = 0
        every = timedelta(minutes=int(self.cfg["runtime"]["ceo_review_minutes"]))
        for o in self.db.active_objectives():
            busy = self.db.one("SELECT COUNT(*) FROM tasks WHERE agent='pdg' AND objective_id=? AND status IN ('pending','running')", (o["id"],))[0]
            if busy:
                continue
            last = datetime.fromisoformat(o["last_review_at"]) if o["last_review_at"] else None
            if last and now() - last < every:
                continue
            self.db.add_task(f"Revue de l'objectif #{o['id']}", "pdg", REVIEW_TEMPLATE.format(oid=o["id"], text=o["text"]),
                             priority=2, objective_id=o["id"], created_by="systeme")
            self.db.run("UPDATE objectives SET last_review_at=? WHERE id=?", (iso(), o["id"]))
            created += 1
        return created

    def cycle(self) -> dict:
        backup.daily(self.cfg, self.db)
        commerce.sync_all(self.cfg, self.db)        # ventes Stripe et emails reçus (sans coût d'API)
        stats = self._cycle()
        try:
            notify.check(self.cfg, self.db, stats)
        except Exception as e:  # une notification ratée ne doit jamais arrêter le moteur
            self.db.log("systeme", "notification", f"Erreur de notification : {e}")
        return stats

    def _cycle(self) -> dict:
        stats = {"ran": 0, "paused": None, "reviews": 0, "error": None}
        self.db.kv_set("last_cycle_at", iso())
        try:
            self.guard.check()
        except BudgetExceeded as e:
            if self._paused_reason != str(e):
                self.db.log("finance", "budget", f"Pause : {e}")
                self._paused_reason = str(e)
            stats["paused"] = str(e)
            return stats
        self._paused_reason = None
        stats["reviews"] = self.schedule_reviews()
        tried: set[int] = set()
        for _ in range(int(self.cfg["runtime"]["max_tasks_per_cycle"])):
            # Une tâche n'est tentée qu'une fois par cycle (pas de nouvel essai immédiat après une erreur).
            nxt = [t for t in self.db.next_tasks(50) if t["id"] not in tried]
            if not nxt:
                break
            t = nxt[0]
            tried.add(t["id"])
            try:
                run_task(self.cfg, self.db, self.llm, self.guard, t)
                stats["ran"] += 1
            except BudgetExceeded as e:
                self.db.log("finance", "budget", f"Pause : {e}")
                self._paused_reason = str(e)
                stats["paused"] = str(e)
                break
            except LLMError as e:
                # Erreur de configuration : inutile d'essayer les autres tâches, on s'arrête pour ce cycle.
                stats["error"] = str(e)
                break
        return stats

    def run_forever(self, echo=print) -> None:
        self.recover()
        pause = int(self.cfg["runtime"]["cycle_seconds"])
        echo(f"Moteur démarré — cycle toutes les {pause} s. Ctrl+C pour arrêter.")
        while True:
            try:
                s = self.cycle()
                if s["ran"] or s["reviews"]:
                    echo(f"[{datetime.now():%H:%M:%S}] {s['ran']} tâche(s) exécutée(s), {s['reviews']} revue(s) planifiée(s). "
                         f"Dépense du jour : {self.db.spent('day'):.4f} $")
                if s["error"]:
                    echo(f"[{datetime.now():%H:%M:%S}] ERREUR de configuration, nouvel essai dans 10 min : {s['error'][:400]}")
                    time.sleep(600)
                    continue
                elif s["paused"]:
                    echo(f"[{datetime.now():%H:%M:%S}] En pause : {s['paused']}")
                    time.sleep(max(pause, 600))
                    continue
                time.sleep(pause if not s["ran"] else 2)
            except KeyboardInterrupt:
                echo("Arrêt demandé. À bientôt.")
                return
