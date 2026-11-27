"""Base de données locale SQLite : tâches, objectifs, mémoire, coûts, validations, journal."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
        self.conn.commit()

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
    def add_objective(self, text: str) -> int:
        return self.run("INSERT INTO objectives(text, created_at) VALUES (?,?)", (text, iso()))

    def active_objectives(self):
        return self.q("SELECT * FROM objectives WHERE status='active' ORDER BY id")

    # ---------- tâches ----------
    def add_task(self, title: str, agent: str, description: str = "", priority: int = 3,
                 parent_id: int | None = None, objective_id: int | None = None,
                 created_by: str = "owner", depth: int = 0) -> int:
        t = iso()
        return self.run(
            "INSERT INTO tasks(title, description, agent, priority, parent_id, objective_id, created_by, depth, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (title, description, agent, max(1, min(5, int(priority))), parent_id, objective_id, created_by, depth, t, t))

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
        return self.q(f"SELECT * FROM memory WHERE ({cond}) AND {vis} ORDER BY id DESC LIMIT ?", tuple(params))

    # ---------- coûts ----------
    def add_cost(self, agent: str, task_id, model: str, inp: int, out: int, searches: int, usd: float) -> None:
        n = now()
        self.run("INSERT INTO costs(ts, day, month, agent, task_id, model, input_tokens, output_tokens, web_searches, usd)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (iso(n), n.strftime("%Y-%m-%d"), n.strftime("%Y-%m"), agent, task_id, model, inp, out, searches, usd))

    def spent(self, period: str = "day") -> float:
        n = now()
        if period == "day":
            r = self.one("SELECT COALESCE(SUM(usd),0) FROM costs WHERE day=?", (n.strftime("%Y-%m-%d"),))
        else:
            r = self.one("SELECT COALESCE(SUM(usd),0) FROM costs WHERE month=?", (n.strftime("%Y-%m"),))
        return float(r[0])

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
