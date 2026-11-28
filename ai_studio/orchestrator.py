"""Le cœur qui tourne 24 h/24 : revues d'objectifs par le PDG, exécution des tâches, respect des budgets."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import backup, commerce, notify, security
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
        today = now().strftime("%Y-%m-%d")
        if self.cfg.get("security", "daily_review", default=True) and self.db.kv_get("security_review_day") != today:
            self.db.kv_set("security_review_day", today)
            self.db.add_task("Revue de sécurité quotidienne", "securite",
                             "Lance security_audit, puis analyse les constats et les signaux de sécurité. Incident critique : "
                             "security_alert niveau critique avec une consigne claire pour le propriétaire (quoi faire, où, en "
                             "combien de minutes) ; point d'attention : security_alert niveau attention (transmis au PDG). Les "
                             "dépenses ne sont pas ton domaine. Ne signale pas ce qui est déjà connu et sans changement depuis hier "
                             "(recall). Termine par un résumé court ; mémorise (remember) seulement les nouveaux constats.",
                             priority=3, created_by="systeme")
            created += 1
        if self.cfg.get("finance", "daily_review", default=True) and self.db.kv_get("finance_review_day") != today:
            self.db.kv_set("finance_review_day", today)
            self.db.add_task("Contrôle quotidien des dépenses", "finance",
                             "Lance spending_check. Pour chaque anomalie, cherche d'abord une cause interne (tâches et missions "
                             "en cours, list_tasks). Si une dérive reste inexpliquée, signale-la au PDG avec les faits "
                             "(delegate_task) : il décidera de la suite. Ne contacte pas le propriétaire. Termine par un résumé "
                             "en 3 lignes ; mémorise (remember) seulement un constat nouveau.",
                             priority=3, created_by="systeme")
            created += 1
        every = timedelta(minutes=int(self.cfg["runtime"]["ceo_review_minutes"]))
        for o in self.db.active_objectives():
            if o["simple"]:               # mission simple : son unique tâche suffit, le propriétaire vérifie le résultat
                continue
            who = o["project_manager"] or "pdg"
            busy = self.db.one("SELECT COUNT(*) FROM tasks WHERE agent=? AND objective_id=? AND status IN ('pending','running')", (who, o["id"]))[0]
            if busy:
                continue
            if o["project_manager"]:      # mission pilotée et à l'arrêt : relance immédiate, sans attendre le point périodique
                from .missions import relaunch_mission
                if relaunch_mission(self.db, "objectif", o["id"]):
                    self.db.run("UPDATE objectives SET last_review_at=? WHERE id=?", (iso(), o["id"]))
                    created += 1
                    continue
            last = datetime.fromisoformat(o["last_review_at"]) if o["last_review_at"] else None
            if last and now() - last < every:
                continue
            if o["project_manager"]:
                self.db.add_task(f"Point de mission #{o['id']}", who, "Point périodique : lance mission_status, fais avancer ce "
                                 "qui est bloqué ou à l'arrêt, ou clôture si la mission est réellement terminée.",
                                 priority=2, objective_id=o["id"], created_by="systeme")
            else:
                self.db.add_task(f"Revue de l'objectif #{o['id']}", "pdg", REVIEW_TEMPLATE.format(oid=o["id"], text=o["text"]),
                                 priority=2, objective_id=o["id"], created_by="systeme")
            self.db.run("UPDATE objectives SET last_review_at=? WHERE id=?", (iso(), o["id"]))
            created += 1
        return created

    def cycle(self) -> dict:
        backup.daily(self.cfg, self.db)
        day = iso()[:10]
        if self.db.kv_get("cache_purged_on") != day:           # bibliothèque de recherches : 30 jours de rétention
            self.db.purge_cache(30); self.db.kv_set("cache_purged_on", day)
        commerce.sync_all(self.cfg, self.db)        # ventes Stripe et emails reçus (sans coût d'API)
        try:
            from . import prospects
            prospects.due_followups(self.cfg, self.db)   # relance unique à J+7, puis « sans suite » (sans coût d'API)
            from . import aftercare
            aftercare.due(self.cfg, self.db)             # suivi après livraison (remerciement, avis, offre suivante)
        except Exception as e:
            self.db.log("systeme", "prospect", f"Suivi des prospects ou des clients impossible : {e}")
        try:
            security.check(self.cfg, self.db)       # alertes de configuration critiques (sans coût d'API)
        except Exception as e:
            self.db.log("systeme", "securite", f"Contrôle de sécurité impossible : {e}")
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
        from . import mail_watch
        from .db import DB
        if mail_watch.start(self.cfg, lambda: DB(self.cfg.db_path)):
            echo("Boîte mail surveillée en temps réel : notification dès qu'un email arrive.")
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
