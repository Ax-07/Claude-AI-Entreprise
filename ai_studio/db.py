"""Base de données locale SQLite : tâches, objectifs, mémoire, coûts, validations, journal."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Activités de l'entreprise (modèle divisionnel avec services partagés). Les tâches sans activité sont « transverses ».
DEFAULT_ACTIVITIES = [
    ("services", "Services numériques", "Diagnostic SEO local et services sur mesure aux entreprises.", "active"),
    ("video", "Studio vidéo", "Chaîne YouTube internationale (vidéos originales, publication validée par le propriétaire).", "preparation"),
    ("ecommerce", "E-commerce", "Vente en dropshipping à l'international (fournisseurs, boutique, logistique).", "preparation"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS objectives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',          -- active | done | paused
    created_at TEXT NOT NULL,
    last_review_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',          -- pending | running | done | failed | blocked
    priority INTEGER NOT NULL DEFAULT 3,             -- 1 (urgent) .. 5 (faible)
    parent_id INTEGER,
    objective_id INTEGER,
    created_by TEXT NOT NULL DEFAULT 'owner',
    depth INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    topic TEXT NOT NULL,
    content TEXT NOT NULL,
    shared INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    month TEXT NOT NULL,
    agent TEXT NOT NULL,
    task_id INTEGER,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    web_searches INTEGER NOT NULL DEFAULT 0,
    usd REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    task_id INTEGER,
    action TEXT NOT NULL,
    payload TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',          -- pending | approved | rejected | executed | error
    result TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    task_id INTEGER
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sales (
    id TEXT PRIMARY KEY,                             -- identifiant Stripe (session de paiement)
    created_at TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    customer_email TEXT,
    customer_name TEXT,
    product TEXT,
    payment_link TEXT,
    livemode INTEGER NOT NULL DEFAULT 1,
    task_id INTEGER,                                 -- tâche de livraison créée
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER,
    message_id TEXT,
    from_addr TEXT NOT NULL,
    from_name TEXT,
    subject TEXT,
    received_at TEXT,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',              -- new | queued | ignored | replied
    reason TEXT,
    task_id INTEGER,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbox_msgid ON inbox(message_id);
CREATE TABLE IF NOT EXISTS web_cache (               -- bibliothèque de recherches : résultats web réutilisables
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                              -- search | page
    key TEXT NOT NULL,                               -- requête normalisée (pays|requête) ou URL
    agent TEXT NOT NULL,
    task_id INTEGER,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_cache ON web_cache(kind, key);
CREATE TABLE IF NOT EXISTS chat_threads (           -- discussions avec le PDG
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,                              -- owner | pdg
    text TEXT NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activities (
    id TEXT PRIMARY KEY,                             -- identifiant court : services, video, ecommerce…
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',           -- active | preparation | pause
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prospects (              -- fichier prospects : qui a été contacté, quand, avec quelle réponse
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,                      -- en minuscules
    name TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    site TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',                 -- où l'adresse professionnelle a été trouvée (URL) : obligatoire (RGPD)
    offer TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'nouveau',          -- nouveau | a_valider | contacte | relance | repondu | interesse | client | refus | stop | sans_suite | invalide
    objective_id INTEGER,
    first_contact_at TEXT,
    last_contact_at TEXT,
    next_action_at TEXT,                             -- date de la relance prévue
    replied_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_rules (            -- consignes permanentes validées par le propriétaire (proposées par l'auditeur RH)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    rule TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    approval_id INTEGER,
    created_at TEXT NOT NULL,
    removed_at TEXT
);
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    price_eur REAL NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    deliverable TEXT NOT NULL DEFAULT '',            -- ce que reçoit le client
    delay_hours INTEGER NOT NULL DEFAULT 72,         -- délai de livraison promis
    required_info TEXT NOT NULL DEFAULT '',          -- ce qu'il faut demander au client pour livrer
    producer TEXT NOT NULL DEFAULT 'atelier',        -- agent qui produit le livrable
    status TEXT NOT NULL DEFAULT 'active',           -- active | archived
    payment_link_id TEXT,
    payment_link_url TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority, id);
CREATE INDEX IF NOT EXISTS idx_costs_day ON costs(day);
CREATE INDEX IF NOT EXISTS idx_memory_topic ON memory(topic);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    # Colonnes ajoutées après coup : ajoutées aux bases existantes sans perte de données.
    MIGRATIONS = (
        ("chat", "thread_id", "INTEGER"),                 # discussion à laquelle appartient le message
        ("offers", "pending", "TEXT"),
        ("offers", "next_offer", "TEXT"),                 # offre à proposer ensuite au client satisfait (après-livraison)
        ("sales", "aftercare_at", "TEXT"),                # suivi après livraison lancé (remerciement, avis, suite)
        ("sales", "feedback", "TEXT"),                    # retour du client après livraison
        ("sales", "satisfied", "INTEGER"),                # 1 satisfait, 0 insatisfait, NULL inconnu
        ("sales", "info_received_at", "TEXT"),            # informations du client reçues : le délai promis démarre
        ("sales", "content_review", "TEXT"),              # relecture du fond par le chef de projet (avant livraison)
        ("sales", "content_review_at", "TEXT"),
        ("objectives", "owner_check", "TEXT"),            # vérification du propriétaire : ok | issue
        ("objectives", "owner_note", "TEXT"),
        ("objectives", "checked_at", "TEXT"),
        ("objectives", "done_at", "TEXT"),
        ("sales", "owner_check", "TEXT"),
        ("objectives", "project_manager", "TEXT"),        # mission confiée à un chef de projet (agent chef_projet)
        ("objectives", "pm_summary", "TEXT"),             # bilan de clôture du chef de projet
        ("sales", "project_manager", "TEXT"),
        ("objectives", "simple", "INTEGER"),               # mission simple (une tâche, un agent) : pas de revue du PDG
        ("sales", "checked_at", "TEXT"),
        ("tasks", "order_ref", "INTEGER"),                # commande concernée (suivi jusqu'à la livraison)
        ("sales", "offer_id", "INTEGER"),
        ("sales", "stage", "TEXT"),                       # a_livrer | attente_client | livree | historique
        ("sales", "due_at", "TEXT"),
        ("sales", "delivered_at", "TEXT"),
        ("sales", "nudges", "INTEGER NOT NULL DEFAULT 0"),
        ("sales", "last_nudge_at", "TEXT"),
        ("sales", "stage_note", "TEXT"),
        ("sales", "owner_notes", "TEXT"),
        ("approvals", "review", "TEXT"),                  # avis de l'agent juridique
        ("approvals", "review_task", "INTEGER"),          # tâche de relecture (0 = pas de relecture)
        ("approvals", "notified", "INTEGER"),             # le propriétaire a été notifié de cette demande
        # activité de l'entreprise (services, vidéo, e-commerce…) : coûts et revenus mesurés par activité
        ("objectives", "activity", "TEXT"),
        ("tasks", "activity", "TEXT"),
        ("costs", "activity", "TEXT"),
        ("offers", "activity", "TEXT"),                  # consignes du propriétaire sur la commande (font foi)
    )

    def _migrate(self):
        for table, col, ddl in self.MIGRATIONS:
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        # Ventes antérieures au suivi des commandes : celles qui avaient une tâche de livraison restent à livrer.
        for aid, name, desc, status in DEFAULT_ACTIVITIES:
            self.conn.execute("INSERT OR IGNORE INTO activities(id, name, description, status, created_at) VALUES (?,?,?,?,?)",
                              (aid, name, desc, status, iso()))
        # notifications : les demandes déjà signalées avant le suivi par demande ne sont pas renotifiées
        last = self.conn.execute("SELECT v FROM kv WHERE k='notified_approval_id'").fetchone()
        if last and str(last[0] or "").isdigit():
            self.conn.execute("UPDATE approvals SET notified=1 WHERE notified IS NULL AND id<=?", (int(last[0]),))
        # tout ce qui touche aux commandes et aux offres existantes relève des services numériques (seule activité jusqu'ici)
        self.conn.execute("UPDATE offers SET activity='services' WHERE activity IS NULL")
        self.conn.execute("UPDATE tasks SET activity='services' WHERE activity IS NULL AND order_ref IS NOT NULL")
        self.conn.execute("UPDATE costs SET activity=(SELECT t.activity FROM tasks t WHERE t.id=costs.task_id) "
                          "WHERE activity IS NULL AND task_id IS NOT NULL")
        # messages échangés avant les discussions multiples : regroupés dans une première discussion
        if self.conn.execute("SELECT 1 FROM chat WHERE thread_id IS NULL LIMIT 1").fetchone():
            first = self.conn.execute("SELECT MIN(created_at), MAX(created_at) FROM chat WHERE thread_id IS NULL").fetchone()
            tid = self.conn.execute("INSERT INTO chat_threads(title, created_at, updated_at) VALUES (?,?,?)",
                                    ("Première discussion", first[0], first[1])).lastrowid
            self.conn.execute("UPDATE chat SET thread_id=? WHERE thread_id IS NULL", (tid,))
        # commandes ouvertes avant les chefs de projet : elles en reçoivent un (les relances iront vers lui)
        self.conn.execute("UPDATE sales SET project_manager='chef_projet' WHERE project_manager IS NULL AND stage IN ('a_livrer','attente_client')")
        # le rapport du diagnostic est désormais livré en PDF (converti au moment de la livraison)
        self.conn.execute("UPDATE offers SET deliverable=replace(deliverable, 'Rapport HTML (s''ouvre dans le navigateur, imprimable en PDF)', "
                          "'Rapport PDF (format A4, paginé)') WHERE deliverable LIKE 'Rapport HTML (s''ouvre dans le navigateur%'")
        self.conn.execute("UPDATE sales SET stage = CASE WHEN task_id IS NULL THEN 'historique' ELSE 'a_livrer' END WHERE stage IS NULL")
        self.conn.execute("UPDATE tasks SET order_ref = (SELECT s.rowid FROM sales s WHERE s.task_id = tasks.id) "
                          "WHERE order_ref IS NULL AND id IN (SELECT task_id FROM sales WHERE task_id IS NOT NULL)")

    # ---------- générique ----------
    def q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()):
        return self.conn.execute(sql, params).fetchone()

    def run(self, sql: str, params: tuple = ()) -> int:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.lastrowid

    # ---------- valeurs clés (état du moteur) ----------
    def kv_set(self, k: str, v: str) -> None:
        self.run("INSERT INTO kv(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def kv_get(self, k: str, default: str | None = None) -> str | None:
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        return r[0] if r else default

    # ---------- journal ----------
    def log(self, agent: str, kind: str, message: str, task_id: int | None = None) -> None:
        self.run("INSERT INTO events(ts, agent, kind, message, task_id) VALUES (?,?,?,?,?)",
                 (iso(), agent, kind, message[:2000], task_id))

    # ---------- objectifs ----------
    def add_objective(self, text: str, activity: str | None = None) -> int:
        return self.run("INSERT INTO objectives(text, created_at, activity) VALUES (?,?,?)", (text, iso(), activity))

    def active_objectives(self):
        return self.q("SELECT * FROM objectives WHERE status='active' ORDER BY id")

    # ---------- tâches ----------
    def add_task(self, title: str, agent: str, description: str = "", priority: int = 3,
                 parent_id: int | None = None, objective_id: int | None = None,
                 created_by: str = "owner", depth: int = 0, order_ref: int | None = None) -> int:
        t = iso()
        activity = None
        if parent_id is not None:          # une sous-tâche reste rattachée à la même mission, commande et activité
            p = self.one("SELECT order_ref, activity, objective_id FROM tasks WHERE id=?", (parent_id,))
            if p:
                order_ref = order_ref if order_ref is not None else p[0]
                objective_id = objective_id if objective_id is not None else p[2]
                activity = p[1]
        if activity is None and objective_id is not None:
            o = self.one("SELECT activity FROM objectives WHERE id=?", (objective_id,))
            activity = o[0] if o else None
        if activity is None and order_ref is not None:
            activity = "services"
        return self.run(
            "INSERT INTO tasks(title, description, agent, priority, parent_id, objective_id, created_by, depth, created_at, updated_at, order_ref, activity)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (title, description, agent, max(1, min(5, int(priority))), parent_id, objective_id, created_by, depth, t, t, order_ref, activity))

    def task(self, task_id: int):
        return self.one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def set_task(self, task_id: int, **fields) -> None:
        fields["updated_at"] = iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.run(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), task_id))

    def next_tasks(self, limit: int):
        return self.q("SELECT * FROM tasks WHERE status='pending' ORDER BY priority, id LIMIT ?", (limit,))

    def open_task_count(self) -> int:
        return self.one("SELECT COUNT(*) FROM tasks WHERE status IN ('pending','running','blocked')")[0]

    # ---------- mémoire ----------
    def remember(self, agent: str, topic: str, content: str, shared: bool = True) -> int:
        return self.run("INSERT INTO memory(agent, topic, content, shared, created_at) VALUES (?,?,?,?,?)",
                        (agent, topic.strip().lower()[:80], content[:4000], 1 if shared else 0, iso()))

    def recall(self, query: str, agent: str | None = None, limit: int = 8):
        words = [w for w in query.lower().split() if len(w) > 2][:6] or [query.lower()]
        cond = " OR ".join("(topic LIKE ? OR lower(content) LIKE ?)" for _ in words)
        params: list = []
        for w in words:
            params += [f"%{w}%", f"%{w}%"]
        vis = "(shared=1 OR agent=?)" if agent else "1=1"
        if agent:
            params.append(agent)
        params.append(limit)
        # Les consignes du propriétaire passent en premier : elles corrigent ce que les agents ont pu mémoriser de faux.
        return self.q(f"SELECT * FROM memory WHERE ({cond}) AND {vis} ORDER BY (agent='proprietaire') DESC, id DESC LIMIT ?",
                      tuple(params))

    # ---------- coûts ----------
    def add_cost(self, agent: str, task_id, model: str, inp: int, out: int, searches: int, usd: float) -> None:
        n = now()
        act = None
        if task_id is not None:
            r = self.one("SELECT activity FROM tasks WHERE id=?", (task_id,))
            act = r[0] if r else None
        self.run("INSERT INTO costs(ts, day, month, agent, task_id, model, input_tokens, output_tokens, web_searches, usd, activity)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (iso(n), n.strftime("%Y-%m-%d"), n.strftime("%Y-%m"), agent, task_id, model, inp, out, searches, usd, act))

    def spent(self, period: str = "day") -> float:
        n = now()
        if period == "day":
            r = self.one("SELECT COALESCE(SUM(usd),0) FROM costs WHERE day=?", (n.strftime("%Y-%m-%d"),))
        else:
            r = self.one("SELECT COALESCE(SUM(usd),0) FROM costs WHERE month=?", (n.strftime("%Y-%m"),))
        return float(r[0])

    # ---------- bibliothèque de recherches ----------
    def cache_get(self, kind: str, key: str, max_age_days: float):
        since = iso(now() - timedelta(days=max_age_days))
        return self.one("SELECT * FROM web_cache WHERE kind=? AND key=? AND created_at>=? ORDER BY id DESC LIMIT 1", (kind, key, since))

    def cache_put(self, kind: str, key: str, agent: str, task_id, content: str) -> int:
        return self.run("INSERT INTO web_cache(kind, key, agent, task_id, content, created_at) VALUES (?,?,?,?,?,?)",
                        (kind, key, agent, task_id, content, iso()))

    def cache_search(self, query: str, limit: int = 8):
        words = [w for w in query.lower().split() if len(w) > 2][:6] or [query.lower().strip()]
        sql = "SELECT * FROM web_cache WHERE " + " AND ".join(["(lower(key) LIKE ? OR lower(content) LIKE ?)"] * len(words))
        params = [x for w in words for x in (f"%{w}%", f"%{w}%")]
        return self.q(sql + " ORDER BY id DESC LIMIT ?", (*params, int(limit)))

    def purge_cache(self, days: int = 30) -> int:
        cur = self.conn.execute("DELETE FROM web_cache WHERE created_at < ?", (iso(now() - timedelta(days=days)),))
        self.conn.commit()
        return cur.rowcount

    def searches_today(self) -> int:
        return int(self.one("SELECT COALESCE(SUM(web_searches),0) FROM costs WHERE day=?", (now().strftime("%Y-%m-%d"),))[0])

    def task_cost(self, task_id: int) -> float:
        return float(self.one("SELECT COALESCE(SUM(usd),0) FROM costs WHERE task_id=?", (task_id,))[0])

    # ---------- validations ----------
    def add_approval(self, agent: str, task_id, action: str, payload: dict, reason: str) -> int:
        return self.run("INSERT INTO approvals(agent, task_id, action, payload, reason, created_at) VALUES (?,?,?,?,?,?)",
                        (agent, task_id, action, json.dumps(payload, ensure_ascii=False), reason, iso()))

    def approval(self, approval_id: int):
        return self.one("SELECT * FROM approvals WHERE id=?", (approval_id,))

    def set_approval(self, approval_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.run(f"UPDATE approvals SET {cols} WHERE id=?", (*fields.values(), approval_id))

    def pending_approvals(self):
        return self.q("SELECT * FROM approvals WHERE status='pending' ORDER BY id")
