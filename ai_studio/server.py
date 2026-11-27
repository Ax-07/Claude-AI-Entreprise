"""API locale + tableau de bord Command HQ.

Lancement : python -m ai_studio serveur   →   http://127.0.0.1:8765

Sécurité :
- écoute uniquement sur 127.0.0.1 (jamais sur le réseau) ;
- l'en-tête Host doit être 127.0.0.1 ou localhost (protection contre le « DNS rebinding ») ;
- toute requête qui modifie quelque chose (POST) doit porter l'en-tête X-AI-Studio: 1, qu'une page web
  d'un autre site ne peut pas envoyer sans autorisation CORS : un site malveillant ouvert dans ton
  navigateur ne peut donc pas valider une action à ta place.
"""
from __future__ import annotations

import json
import sys
import mimetypes
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .agents import ROLES
from .db import DB, now

WEB_DIR = Path(__file__).resolve().parent / "web"
POLE_COLORS = {"pdg": "#818CF8", "recherche": "#34D399", "marketing": "#60A5FA", "ventes": "#A78BFA",
               "support": "#FBBF24", "data": "#22D3EE", "finance": "#F472B6"}
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _row(r) -> dict:
    return {k: r[k] for k in r.keys()}


# ====================================================================== lecture des données
def engine_state(cfg, db) -> dict:
    last = db.kv_get("last_cycle_at")
    alive = False
    if last:
        age = (now() - datetime.fromisoformat(last)).total_seconds()
        alive = age < max(180, int(cfg["runtime"]["cycle_seconds"]) * 3 + 120)
    paused = db.one("SELECT message, ts FROM events WHERE kind='budget' ORDER BY id DESC LIMIT 1")
    return {"last_cycle_at": last, "running": alive,
            "last_budget_event": _row(paused) if paused else None}


def status(cfg, db) -> dict:
    from .llm import models_for, resolve_provider
    import os
    b = cfg["budget"]
    counts = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM tasks GROUP BY status")}
    done = counts.get("done", 0)
    executed = db.one("SELECT COUNT(*) FROM approvals WHERE status='executed'")[0]
    xp = done * 10 + executed * 25
    level = 1 + xp // 100
    today = now().strftime("%Y-%m-%d")
    done_today = db.one("SELECT COUNT(*) FROM tasks WHERE status='done' AND substr(updated_at,1,10)=?", (today,))[0]
    active_agents = db.one("SELECT COUNT(DISTINCT agent) FROM tasks WHERE status IN ('running','pending')")[0]
    objective = db.one("SELECT * FROM objectives WHERE status='active' ORDER BY id DESC LIMIT 1")
    obj = None
    if objective:
        tot = db.one("SELECT COUNT(*) FROM tasks WHERE objective_id=?", (objective["id"],))[0]
        fin = db.one("SELECT COUNT(*) FROM tasks WHERE objective_id=? AND status='done'", (objective["id"],))[0]
        obj = {**_row(objective), "tasks_total": tot, "tasks_done": fin}
    return {
        "company": cfg["company"]["name"],
        "provider": resolve_provider(cfg), "models": models_for(cfg),
        "web_search": "brave" if os.environ.get("BRAVE_API_KEY") else ("anthropic" if resolve_provider(cfg) == "anthropic" else None),
        "engine": engine_state(cfg, db),
        "budget": {"day": round(db.spent("day"), 4), "day_limit": b["daily_usd"],
                   "month": round(db.spent("month"), 4), "month_limit": b["monthly_usd"],
                   "searches": db.searches_today(), "searches_limit": b["web_searches_per_day"]},
        "tasks": {"pending": counts.get("pending", 0), "running": counts.get("running", 0), "done": done,
                  "failed": counts.get("failed", 0), "blocked": counts.get("blocked", 0), "done_today": done_today},
        "active_agents": active_agents, "agents_total": len(ROLES),
        "approvals_pending": len(db.pending_approvals()),
        "xp": xp, "level": level, "level_progress": xp % 100,
        "objective": obj,
        "achievements": achievements(db, done),
        "stripe": bool(os.environ.get("STRIPE_SECRET_KEY")),
        "revenue": revenue(db),
    }


def revenue(db) -> dict:
    from .commerce import revenue as rev
    return rev(db)


def sales(cfg, db) -> list[dict]:
    return [_row(r) for r in db.q("SELECT * FROM sales ORDER BY created_at DESC LIMIT 100")]


def inbox(cfg, db) -> list[dict]:
    return [_row(r) for r in db.q("SELECT id, from_addr, from_name, subject, received_at, body, status, reason, task_id "
                                  "FROM inbox ORDER BY id DESC LIMIT 100")]


def page_preview(cfg, db, approval_id: int) -> bytes:
    a = db.approval(approval_id)
    if not a or a["action"] != "publish_page":
        raise ApiError(404, "Aucune page à prévisualiser")
    return json.loads(a["payload"])["html"].encode("utf-8")


def achievements(db, done: int) -> list[dict]:
    over_days = db.one("SELECT COUNT(*) FROM events WHERE kind='budget' AND message LIKE 'Pause : Budget du jour%'")[0]
    days_active = db.one("SELECT COUNT(DISTINCT day) FROM costs")[0]
    files = db.one("SELECT COUNT(*) FROM events WHERE kind='file'")[0]
    return [
        {"name": "Premier cycle réussi", "desc": "Une première tâche menée à bien", "value": min(done, 1), "max": 1},
        {"name": "Équipe rodée", "desc": "100 tâches terminées", "value": min(done, 100), "max": 100},
        {"name": "Livrables", "desc": "25 fichiers produits par les agents", "value": min(files, 25), "max": 25},
        {"name": "Budget maîtrisé", "desc": "Jours d'activité sans dépasser le plafond", "value": max(0, days_active - over_days), "max": 30},
    ]


def agents(cfg, db) -> list[dict]:
    from .llm import models_for
    m = models_for(cfg)
    month = now().strftime("%Y-%m")
    out = []
    for r in ROLES.values():
        running = db.one("SELECT id, title FROM tasks WHERE agent=? AND status='running' ORDER BY id LIMIT 1", (r.name,))
        pending = db.one("SELECT COUNT(*) FROM tasks WHERE agent=? AND status='pending'", (r.name,))[0]
        done = db.one("SELECT COUNT(*) FROM tasks WHERE agent=? AND status='done'", (r.name,))[0]
        failed = db.one("SELECT COUNT(*) FROM tasks WHERE agent=? AND status='failed'", (r.name,))[0]
        last = db.one("SELECT id, title, status, updated_at FROM tasks WHERE agent=? ORDER BY updated_at DESC LIMIT 1", (r.name,))
        cost = db.one("SELECT COALESCE(SUM(usd),0) FROM costs WHERE agent=? AND month=?", (r.name, month))[0]
        state = "running" if running else ("waiting" if pending else "idle")
        out.append({"name": r.name, "pole": r.pole, "title": r.title, "color": POLE_COLORS.get(r.name, "#94A3B8"),
                    "model": m["strong"] if r.model == "strong" else m["default"], "state": state,
                    "current": _row(running) if running else None, "pending": pending, "done": done, "failed": failed,
                    "last": _row(last) if last else None, "cost_month": round(float(cost), 4),
                    "success_rate": round(100 * done / (done + failed)) if done + failed else None})
    return out


def tasks(cfg, db, status_: str = "", agent: str = "", limit: int = 50) -> list[dict]:
    sql, params = "SELECT id, title, agent, status, priority, created_by, cost_usd, created_at, updated_at, parent_id, objective_id FROM tasks WHERE 1=1", []
    if status_:
        sql += " AND status=?"; params.append(status_)
    if agent:
        sql += " AND agent=?"; params.append(agent)
    sql += " ORDER BY id DESC LIMIT ?"; params.append(max(1, min(int(limit), 200)))
    return [_row(r) for r in db.q(sql, tuple(params))]


def task_detail(cfg, db, task_id: int) -> dict:
    t = db.task(task_id)
    if not t:
        raise ApiError(404, "Tâche introuvable")
    ev = [_row(e) for e in db.q("SELECT ts, agent, kind, message FROM events WHERE task_id=? ORDER BY id", (task_id,))]
    children = [_row(c) for c in db.q("SELECT id, title, agent, status FROM tasks WHERE parent_id=? ORDER BY id", (task_id,))]
    return {**_row(t), "events": ev, "children": children}


def events(cfg, db, since_id: int = 0, limit: int = 40) -> list[dict]:
    rows = db.q("SELECT id, ts, agent, kind, message, task_id FROM events WHERE id>? ORDER BY id DESC LIMIT ?",
                (int(since_id), max(1, min(int(limit), 200))))
    return [_row(r) for r in rows]


def approvals(cfg, db) -> list[dict]:
    out = []
    for a in db.pending_approvals():
        d = _row(a)
        d["payload"] = json.loads(a["payload"])
        if "html" in d["payload"]:                     # la page complète est servie à part (aperçu)
            d["payload"]["size"] = len(d["payload"].pop("html").encode("utf-8"))
        out.append(d)
    return out


def costs(cfg, db) -> dict:
    days = [_row(r) for r in db.q("SELECT day, ROUND(SUM(usd),4) usd, SUM(web_searches) searches FROM costs GROUP BY day ORDER BY day DESC LIMIT 14")]
    month = now().strftime("%Y-%m")
    by_agent = [_row(r) for r in db.q("SELECT agent, ROUND(SUM(usd),4) usd, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens "
                                         "FROM costs WHERE month=? GROUP BY agent ORDER BY usd DESC", (month,))]
    return {"days": list(reversed(days)), "by_agent": by_agent}


def objectives(cfg, db) -> list[dict]:
    out = []
    for o in db.q("SELECT * FROM objectives ORDER BY id DESC"):
        tot = db.one("SELECT COUNT(*) FROM tasks WHERE objective_id=?", (o["id"],))[0]
        fin = db.one("SELECT COUNT(*) FROM tasks WHERE objective_id=? AND status='done'", (o["id"],))[0]
        out.append({**_row(o), "tasks_total": tot, "tasks_done": fin})
    return out


def _workspace_path(cfg, rel: str) -> Path:
    base = cfg.workspace.resolve()
    p = (base / (rel or ".").strip().lstrip("/\\")).resolve()
    if base != p and base not in p.parents:
        raise ApiError(400, "Chemin hors de l'espace de travail")
    return p


def files(cfg, db) -> list[dict]:
    base = cfg.workspace.resolve()
    out = []
    for f in sorted(base.rglob("*")):
        if f.is_file():
            st = f.stat()
            out.append({"path": str(f.relative_to(base)).replace("\\", "/"), "size": st.st_size,
                        "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")})
    out.sort(key=lambda x: x["modified"], reverse=True)
    return out[:300]


def file_content(cfg, db, rel: str) -> dict:
    p = _workspace_path(cfg, rel)
    if not p.is_file():
        raise ApiError(404, "Fichier introuvable")
    return {"path": rel, "content": p.read_text(encoding="utf-8", errors="replace")[:300_000]}


def insights(cfg, db, days: int = 7) -> dict:
    """Séries réelles pour le panneau Performance + pipeline commercial + prochaines échéances."""
    days = 30 if int(days) >= 30 else 7
    today = now().date()
    labels, done, cost, rate = [], [], [], []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        n_done = db.one("SELECT COUNT(*) FROM tasks WHERE status='done' AND substr(updated_at,1,10)=?", (d,))[0]
        n_fail = db.one("SELECT COUNT(*) FROM tasks WHERE status='failed' AND substr(updated_at,1,10)=?", (d,))[0]
        usd = db.one("SELECT COALESCE(SUM(usd),0) FROM costs WHERE day=?", (d,))[0]
        labels.append(d); done.append(n_done); cost.append(round(float(usd), 4))
        rate.append(round(100 * n_done / (n_done + n_fail)) if n_done + n_fail else None)
    count = lambda sql: db.one(sql)[0]
    pipeline = [
        {"stage": "Pistes en mémoire", "value": count("SELECT COUNT(*) FROM memory")},
        {"stage": "Emails préparés", "value": count("SELECT COUNT(*) FROM approvals WHERE action='send_email'")},
        {"stage": "Emails envoyés", "value": count("SELECT COUNT(*) FROM approvals WHERE action='send_email' AND status='executed'")},
        {"stage": "Emails reçus", "value": count("SELECT COUNT(*) FROM inbox WHERE status!='ignored'"), "open": "inbox"},
        {"stage": "Pages publiées", "value": count("SELECT COUNT(DISTINCT json_extract(payload,'$.path')) FROM approvals WHERE action='publish_page' AND status='executed'")},
        {"stage": "Liens de paiement créés", "value": count("SELECT COUNT(*) FROM approvals WHERE action='stripe_payment_link' AND status='executed'")},
        {"stage": "Ventes", "value": count("SELECT COUNT(*) FROM sales"), "open": "sales"},
    ]
    schedule = []
    every = timedelta(minutes=int(cfg["runtime"]["ceo_review_minutes"]))
    for o in db.active_objectives():
        nxt = (datetime.fromisoformat(o["last_review_at"]) + every) if o["last_review_at"] else now()
        schedule.append({"at": nxt.isoformat(timespec="seconds"), "title": f"Revue de l'objectif #{o['id']}", "who": "pdg"})
    last = db.kv_get("last_cycle_at")
    if last:
        schedule.append({"at": (datetime.fromisoformat(last) + timedelta(seconds=int(cfg["runtime"]["cycle_seconds"]))).isoformat(timespec="seconds"),
                         "title": "Prochain cycle du moteur", "who": "systeme"})
    midnight = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    schedule.append({"at": midnight.isoformat(timespec="seconds"), "title": "Remise à zéro du budget du jour", "who": "finance"})
    schedule.sort(key=lambda s: s["at"])
    return {"days": labels, "done": done, "cost": cost, "success_rate": rate, "pipeline": pipeline, "schedule": schedule[:5]}


MAP_POLES = {"hq", "research", "marketing", "sales", "support", "data", "finance"}


def map_settings(cfg, db) -> dict:
    try:
        variants = json.loads(db.kv_get("map_variants") or "{}")
    except json.JSONDecodeError:
        variants = {}
    return {"variants": variants, "view": db.kv_get("map_view") or "ville"}


def set_map(cfg, db, body: dict) -> dict:
    cur = map_settings(cfg, db)
    if "view" in body:
        if body["view"] not in ("ville", "bureaux"):
            raise ApiError(400, "view doit valoir ville ou bureaux")
        db.kv_set("map_view", body["view"])
    if "pole" in body:
        pole, var = body.get("pole"), body.get("variant")
        if pole not in MAP_POLES or var not in ("A", "B", "C"):
            raise ApiError(400, "pôle ou variante invalide")
        cur["variants"][pole] = var
        db.kv_set("map_variants", json.dumps(cur["variants"]))
    return {"ok": True, **map_settings(cfg, db)}


def integrations(cfg, db=None) -> list[dict]:
    import os
    from .commerce import imap_settings, site_settings
    from .llm import resolve_provider
    db_err = lambda name: (db.kv_get(f"sync_{name}_error") if db is not None else "") or ""
    p = resolve_provider(cfg)
    return [
        {"name": "Fournisseur d'IA", "ok": bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")), "detail": p},
        {"name": "Brave Search", "ok": bool(os.environ.get("BRAVE_API_KEY")), "detail": "recherche web"},
        {"name": "Stripe", "ok": bool(os.environ.get("STRIPE_SECRET_KEY")),
         "detail": db_err("stripe") or ("ventes lues toutes les 10 min" if os.environ.get("STRIPE_SECRET_KEY") else "paiements")},
        {"name": "Email (SMTP)", "ok": bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")), "detail": "envoi après validation"},
        {"name": "Boîte mail (lecture)", "ok": bool(imap_settings()[1] and imap_settings()[2]),
         "detail": (db_err("inbox") or f"{imap_settings()[1]} — réponses confiées aux agents") if imap_settings()[1] else "IMAP_* ou Gmail dans SMTP_*"},
        {"name": "Site (GitHub Pages)", "ok": bool(site_settings()[0] and site_settings()[1]),
         "detail": site_settings()[3] or "GITHUB_PAGES_REPO et GITHUB_TOKEN"},
        {"name": "Notifications Windows", "ok": sys.platform == "win32" and bool(cfg.get("notifications", "windows", default=True)),
         "detail": "validations, budget, erreurs" if sys.platform == "win32" else "disponibles seulement sous Windows"},
        {"name": "Sauvegarde quotidienne", "ok": bool(cfg.get("backup", "enabled", default=True)),
         "detail": f"data/sauvegardes, {cfg.get('backup', 'keep', default=14)} dernières gardées"},
    ]


# ====================================================================== actions
def decide_approval(cfg, db, approval_id: int, body: dict) -> dict:
    from .approvals import decide
    decision = body.get("decision")
    if decision not in ("approve", "reject"):
        raise ApiError(400, "decision doit valoir 'approve' ou 'reject'")
    if not db.approval(approval_id):
        raise ApiError(404, "Demande introuvable")
    from .approvals import NotReady
    try:
        msg = decide(cfg, db, approval_id, decision == "approve", str(body.get("note") or "")[:1000])
    except NotReady as e:
        raise ApiError(409, str(e))
    return {"ok": True, "message": msg}


def add_objective(cfg, db, body: dict) -> dict:
    text = str(body.get("text") or "").strip()
    if len(text) < 5:
        raise ApiError(400, "Objectif trop court")
    oid = db.add_objective(text[:1000])
    db.log("proprietaire", "objective", f"Nouvel objectif #{oid} : {text[:300]}")
    return {"ok": True, "id": oid}


def set_objective(cfg, db, oid: int, body: dict) -> dict:
    st = body.get("status")
    if st not in ("active", "paused", "done"):
        raise ApiError(400, "status doit valoir active, paused ou done")
    if not db.one("SELECT id FROM objectives WHERE id=?", (oid,)):
        raise ApiError(404, "Objectif introuvable")
    db.run("UPDATE objectives SET status=? WHERE id=?", (st, oid))
    if st != "active":
        db.run("UPDATE tasks SET status='blocked', result='Objectif mis en pause par le propriétaire' WHERE objective_id=? AND status='pending'", (oid,))
    db.log("proprietaire", "objective", f"Objectif #{oid} → {st}")
    return {"ok": True}


def retry_task(cfg, db, task_id: int) -> dict:
    n = db.conn.execute("UPDATE tasks SET status='pending', attempts=0 WHERE id=? AND status IN ('failed','blocked')", (task_id,)).rowcount
    db.conn.commit()
    if not n:
        raise ApiError(400, "Seules les tâches en échec ou bloquées peuvent être relancées")
    db.log("proprietaire", "task", f"Tâche #{task_id} relancée")
    return {"ok": True}


# ====================================================================== serveur HTTP
def make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AIStudio/0.2"

        def log_message(self, fmt, *args):  # silencieux
            pass

        # -- utilitaires
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in ALLOWED_HOSTS

        def _send(self, status: int, data, ctype="application/json; charset=utf-8", gzipped: bool = False, cache: bool = False):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            if gzipped:
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=3600" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                             "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _preview(self, body: bytes):
            """Aperçu d'une page avant publication : aucun script, isolée (sandbox)."""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; sandbox")
            self.end_headers()
            self.wfile.write(body)

        def _db(self):
            return DB(cfg.db_path)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100_000:
                raise ApiError(413, "Requête trop grande")
            raw = self.rfile.read(n) if n else b"{}"
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                raise ApiError(400, "JSON invalide")
            if not isinstance(data, dict):
                raise ApiError(400, "Objet JSON attendu")
            return data

        def _dispatch(self, method: str):
            if not self._host_ok():
                return self._send(403, {"error": "Hôte non autorisé"})
            url = urlparse(self.path)
            path, qs = url.path.rstrip("/") or "/", parse_qs(url.query)
            q = lambda k, d="": (qs.get(k) or [d])[0]
            if method == "GET" and (path == "/" or not path.startswith("/api")):
                return self._static(path)
            if method == "POST" and self.headers.get("X-AI-Studio") != "1":
                return self._send(403, {"error": "En-tête X-AI-Studio manquant"})
            db = self._db()
            try:
                parts = path.split("/")[2:]          # /api/<a>/<b>
                if method == "GET":
                    routes = {
                        "status": lambda: status(cfg, db), "agents": lambda: agents(cfg, db),
                        "approvals": lambda: approvals(cfg, db), "costs": lambda: costs(cfg, db),
                        "objectives": lambda: objectives(cfg, db), "files": lambda: files(cfg, db),
                        "map": lambda: map_settings(cfg, db), "integrations": lambda: integrations(cfg, db),
                        "sales": lambda: sales(cfg, db), "inbox": lambda: inbox(cfg, db),
                    }
                    if parts == ["tasks"]:
                        return self._send(200, tasks(cfg, db, q("status"), q("agent"), int(q("limit", "50") or 50)))
                    if len(parts) == 2 and parts[0] == "tasks" and parts[1].isdigit():
                        return self._send(200, task_detail(cfg, db, int(parts[1])))
                    if len(parts) == 3 and parts[0] == "approvals" and parts[1].isdigit() and parts[2] == "preview":
                        return self._preview(page_preview(cfg, db, int(parts[1])))
                    if parts == ["insights"]:
                        return self._send(200, insights(cfg, db, int(q("days", "7") or 7)))
                    if parts == ["events"]:
                        return self._send(200, events(cfg, db, int(q("since", "0") or 0), int(q("limit", "40") or 40)))
                    if parts == ["file"]:
                        return self._send(200, file_content(cfg, db, q("path")))
                    if len(parts) == 1 and parts[0] in routes:
                        return self._send(200, routes[parts[0]]())
                elif method == "POST":
                    body = self._body()
                    if len(parts) == 2 and parts[0] == "approvals" and parts[1].isdigit():
                        return self._send(200, decide_approval(cfg, db, int(parts[1]), body))
                    if parts == ["objectives"]:
                        return self._send(200, add_objective(cfg, db, body))
                    if parts == ["map"]:
                        return self._send(200, set_map(cfg, db, body))
                    if len(parts) == 2 and parts[0] == "objectives" and parts[1].isdigit():
                        return self._send(200, set_objective(cfg, db, int(parts[1]), body))
                    if len(parts) == 3 and parts[0] == "tasks" and parts[1].isdigit() and parts[2] == "retry":
                        return self._send(200, retry_task(cfg, db, int(parts[1])))
                return self._send(404, {"error": "Route inconnue"})
            except ApiError as e:
                return self._send(e.status, {"error": str(e)})
            except Exception as e:  # ne jamais faire tomber le serveur
                return self._send(500, {"error": f"{type(e).__name__} : {e}"})
            finally:
                db.conn.close()

        def _static(self, path: str):
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            p = (WEB_DIR / name).resolve()
            if WEB_DIR.resolve() not in p.parents or not p.is_file():
                return self._send(404, {"error": "Introuvable"})
            ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml", "application/json"):
                ctype += "; charset=utf-8"
            data = p.read_bytes()
            big = len(data) > 20_000
            if big and "gzip" in (self.headers.get("Accept-Encoding") or ""):
                data, gz = _gzip_cached(p, data), True       # la carte (≈ 640 Ko) tombe à ≈ 100 Ko
            else:
                gz = False
            return self._send(200, data, ctype, gzipped=gz, cache=big)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler


_GZ: dict = {}


def _gzip_cached(path: Path, data: bytes) -> bytes:
    import gzip
    key = (str(path), path.stat().st_mtime)
    if key not in _GZ:
        _GZ[key] = gzip.compress(data, 6)
    return _GZ[key]


def make_server(cfg, port: int = 8765) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg))
    srv.daemon_threads = True
    return srv


def serve_in_thread(cfg, port: int = 8765) -> ThreadingHTTPServer:
    srv = make_server(cfg, port)
    threading.Thread(target=srv.serve_forever, name="ai-studio-http", daemon=True).start()
    return srv
