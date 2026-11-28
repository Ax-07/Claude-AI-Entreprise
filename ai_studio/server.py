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

import hashlib
import json
import re
import sys
import mimetypes
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .agents import DIVISION_OF, ROLES
from .db import DB, now

WEB_DIR = Path(__file__).resolve().parent / "web"
POLE_COLORS = {"pdg": "#818CF8", "recherche": "#34D399", "marketing": "#60A5FA", "ventes": "#A78BFA",
               "support": "#FBBF24", "data": "#F1F5F9", "finance": "#F472B6",
               "atelier": "#FB923C", "developpement": "#22D3EE", "chef_projet": "#A5B4FC", "juridique": "#CBD5E1",
               "securite": "#F87171", "rh": "#E879F9"}
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
    return [_row(r) for r in db.q("SELECT rowid AS n, * FROM sales ORDER BY created_at DESC LIMIT 100")]


def inbox(cfg, db) -> list[dict]:
    return [_row(r) for r in db.q("SELECT id, from_addr, from_name, subject, received_at, body, status, reason, task_id "
                                  "FROM inbox ORDER BY id DESC LIMIT 100")]


def page_preview(cfg, db, approval_id: int) -> bytes:
    a = db.approval(approval_id)
    if not a or a["action"] != "publish_page":
        raise ApiError(404, "Aucune page à prévisualiser")
    return json.loads(a["payload"])["html"].encode("utf-8")


PREVIEW_TYPES = {".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
                 ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff", ".pdf": "application/pdf"}


def deliverable_preview(cfg, db, approval_id: int, sub: str = "") -> tuple[bytes, str]:
    """Aperçu du livrable joint à un email en attente : page HTML, document, ou fichier d'une archive de site."""
    import zipfile
    from pathlib import PurePosixPath
    a = db.approval(approval_id)
    att = json.loads(a["payload"]).get("attachment") if a and a["action"] == "send_email" else None
    if not att:
        raise ApiError(404, "Aucun livrable à prévisualiser")
    base = cfg.workspace.resolve()
    p = (base / att).resolve()
    if base not in p.parents or not p.is_file():
        raise ApiError(404, "Livrable introuvable")
    if p.suffix.lower() == ".zip":
        sub = str(PurePosixPath("/" + (sub or "index.html"))).lstrip("/") or "index.html"
        if sub.endswith("/"):
            sub += "index.html"
        ctype = PREVIEW_TYPES.get(PurePosixPath(sub).suffix.lower())
        if not ctype:
            raise ApiError(404, "Type de fichier non prévisualisable")
        with zipfile.ZipFile(p) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            top = names[0].split("/")[0] + "/" if names and all(n.startswith(names[0].split("/")[0] + "/") for n in names) else ""
            for cand in (top + sub, top + sub + "/index.html"):
                if cand in names:
                    return z.read(cand), PREVIEW_TYPES.get(PurePosixPath(cand).suffix.lower(), ctype)
        raise ApiError(404, f"{sub} absent de l'archive")
    ctype = PREVIEW_TYPES.get(p.suffix.lower())
    if not ctype:
        raise ApiError(404, "Type de fichier non prévisualisable")
    return p.read_bytes(), ctype


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
                    "division": DIVISION_OF.get(r.name, ""),
                    "model": m["strong"] if r.model == "strong" else m["default"], "state": state,
                    "current": _row(running) if running else None, "pending": pending, "done": done, "failed": failed,
                    "level": 1 + done // 10,   # même barème que l'entreprise : 10 XP par tâche réussie, 100 XP par niveau
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
    from .missions import labels_for_tasks
    rows = [_row(r) for r in db.q("SELECT id, ts, agent, kind, message, task_id FROM events WHERE id>? ORDER BY id DESC LIMIT ?",
                                  (int(since_id), max(1, min(int(limit), 200))))]
    labels = labels_for_tasks(db, [r["task_id"] for r in rows])
    for r in rows:
        r["mission"] = labels.get(r["task_id"])           # « Mission #3 · … » ou « Commande n°2 · … »
        if not r["mission"]:
            m = re.search(r"[Cc]ommande n°(\d+)", r["message"])
            if m:
                o = db.one("SELECT product FROM sales WHERE rowid=?", (int(m.group(1)),))
                if o:
                    r["mission"] = {"kind": "commande", "id": int(m.group(1)), "label": f"Commande n°{m.group(1)} · {o['product'] or 'commande'}"}
    return rows


def approvals(cfg, db) -> list[dict]:
    out = []
    for a in db.q("SELECT * FROM approvals WHERE status='review' ORDER BY id"):     # en relecture : visibles, pas encore à décider
        d = _row(a)
        d["payload"] = {k: v for k, v in json.loads(a["payload"]).items() if k != "html"}
        d["in_review"] = True
        out.append(d)
    for a in db.pending_approvals():
        d = _row(a)
        d["payload"] = json.loads(a["payload"])
        if d.get("review_task") and not d.get("review"):
            t = db.task(d["review_task"])
            d["review_pending"] = bool(t and t["status"] in ("pending", "running"))
        if "html" in d["payload"]:                     # la page complète est servie à part (aperçu)
            d["payload"]["size"] = len(d["payload"].pop("html").encode("utf-8"))
        out.append(d)
    return out


def costs(cfg, db) -> dict:
    days = [_row(r) for r in db.q("SELECT day, ROUND(SUM(usd),4) usd, SUM(web_searches) searches FROM costs GROUP BY day ORDER BY day DESC LIMIT 14")]
    month = now().strftime("%Y-%m")
    by_agent = [_row(r) for r in db.q("SELECT agent, ROUND(SUM(usd),4) usd, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens "
                                         "FROM costs WHERE month=? GROUP BY agent ORDER BY usd DESC", (month,))]
    # par mission : coût d'API, et pour une commande le chiffre d'affaires et la marge (en euros)
    from .missions import missions
    rate = float(cfg["prices"].get("usd_to_eur", 0.92))
    sales = {r["n"]: r for r in db.q("SELECT rowid AS n, amount, currency, livemode FROM sales")}
    by_mission = []
    for m in missions(db):
        if not m["cost_usd"] and m["kind"] == "objectif" and not m["total"]:
            continue
        row = {"kind": m["kind"], "id": m["id"], "title": m["title"], "state": m["state"], "tasks": m["total"],
               "cost_usd": m["cost_usd"], "cost_eur": round(m["cost_usd"] * rate, 4), "test": m.get("test", False)}
        s = sales.get(m["id"]) if m["kind"] == "commande" else None
        if s and s["currency"] == "EUR":
            row["revenue_eur"] = s["amount"]
            oc = cfg.get("offers", default={}) or {}       # même calcul que le bilan par offre : Stripe, cotisations, API
            fees = s["amount"] * float(oc.get("stripe_pct", 0.028)) + float(oc.get("stripe_fixed_eur", 0.25))
            row["margin_eur"] = round(s["amount"] - fees - s["amount"] * float(oc.get("social_rate", 0.256)) - row["cost_eur"], 2)
        by_mission.append(row)
    by_mission.sort(key=lambda r: -r["cost_usd"])
    outside = db.one("SELECT COALESCE(SUM(c.usd),0) FROM costs c LEFT JOIN tasks t ON t.id=c.task_id "
                     "WHERE c.month=? AND (t.id IS NULL OR (t.objective_id IS NULL AND t.order_ref IS NULL))", (month,))[0]
    return {"days": list(reversed(days)), "by_agent": by_agent, "by_mission": by_mission, "usd_to_eur": rate,
            "outside_missions_month_usd": round(outside, 4)}


def activities(cfg, db) -> list[dict]:
    from .activities import report
    return report(db)


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


MAP_POLES = {"hq", "data", "marketing", "sales", "support", "research", "atelier", "dev", "legal", "finance", "security", "video", "ecommerce", "rh", "infra", "auto"}  # un bâtiment par agent


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
        if pole not in MAP_POLES or var not in tuple("ABCDEFGHI"):
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
        edits = body.get("edits") if isinstance(body.get("edits"), dict) else None
        msg = decide(cfg, db, approval_id, decision == "approve", str(body.get("note") or "")[:1000], edits)
    except NotReady as e:
        raise ApiError(409, str(e))
    except ValueError as e:
        raise ApiError(400, str(e))
    return {"ok": True, "message": msg}


def add_objective(cfg, db, body: dict) -> dict:
    text = str(body.get("text") or "").strip()
    if len(text) < 5:
        raise ApiError(400, "Objectif trop court")
    from .activities import exists
    act = body.get("activity") or None
    if act and not exists(db, act):
        raise ApiError(400, "Activité inconnue")
    oid = db.add_objective(text[:1000], act)
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

        def _send(self, status: int, data, ctype="application/json; charset=utf-8", gzipped: bool = False, etag: str = ""):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            if etag:
                self.send_header("ETag", etag)
            self.send_header("Content-Type", ctype)
            if gzipped:
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(body)))
            # Fichiers statiques : le navigateur garde une copie mais revérifie à chaque chargement (réponse 304 si
            # rien n'a changé). Ainsi une mise à jour (git pull) est visible immédiatement, sans vider le cache.
            self.send_header("Cache-Control", "no-cache" if etag else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                             "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _preview(self, body: bytes, ctype: str = "text/html; charset=utf-8"):
            """Aperçu d'une page ou d'un livrable avant validation : aucun script, isolé (sandbox)."""
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if ctype == "application/pdf":       # la visionneuse PDF du navigateur ne s'ouvre pas dans une page « sandbox »
                self.send_header("Content-Disposition", "inline")
            else:
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                 "img-src 'self' https: data:; font-src 'self' https: data:; sandbox")
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
                        "activities": lambda: activities(cfg, db),
                    }
                    if parts == ["tasks"]:
                        return self._send(200, tasks(cfg, db, q("status"), q("agent"), int(q("limit", "50") or 50)))
                    if len(parts) == 2 and parts[0] == "tasks" and parts[1].isdigit():
                        return self._send(200, task_detail(cfg, db, int(parts[1])))
                    if len(parts) >= 3 and parts[0] == "approvals" and parts[1].isdigit() and parts[2] == "livrable":
                        return self._preview(*deliverable_preview(cfg, db, int(parts[1]), "/".join(parts[3:])))
                    if len(parts) == 3 and parts[0] == "approvals" and parts[1].isdigit() and parts[2] == "preview":
                        return self._preview(page_preview(cfg, db, int(parts[1])))
                    if parts == ["chat"]:
                        return self._send(200, _chat_history(db, int(q("thread", "0") or 0)))
                    if parts == ["chat", "threads"]:
                        return self._send(200, _chat_threads(db))
                    if parts == ["report"]:
                        import re as _re
                        from .reports import monthly
                        m = q("month", "") or None
                        if m and not _re.fullmatch(r"\d{4}-\d{2}", m):
                            raise ApiError(400, "Mois au format AAAA-MM")
                        return self._send(200, monthly(cfg, db, m))
                    if parts == ["agent-rules"]:
                        return self._send(200, [dict(r) for r in db.q("SELECT id, agent, rule, reason, created_at FROM agent_rules WHERE active=1 ORDER BY agent, id")])
                    if parts == ["prospects"]:
                        from . import prospects
                        return self._send(200, {"prospects": prospects.listing(db, limit=500), "counts": prospects.counts(db)})
                    if parts == ["missions"]:
                        from .missions import missions
                        return self._send(200, missions(db))
                    if len(parts) == 3 and parts[0] == "missions" and parts[1] in ("objectif", "commande") and parts[2].isdigit():
                        from .missions import detail
                        try:
                            return self._send(200, detail(db, parts[1], int(parts[2])))
                        except KeyError as e:
                            raise ApiError(404, str(e))
                    if parts == ["library"]:
                        return self._send(200, library(db, q("q"), int(q("id", "0") or 0)))
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
                    if parts == ["chat"]:
                        return self._send(200, _chat_send(cfg, db, body))
                    if len(parts) == 3 and parts[0] == "missions" and parts[1] in ("objectif", "commande") and parts[2].isdigit():
                        from .missions import verify
                        try:
                            return self._send(200, {"ok": True, "message": verify(cfg, db, parts[1], int(parts[2]), body.get("verdict"), str(body.get("note") or "")[:1000])})
                        except KeyError as e:
                            raise ApiError(404, str(e))
                        except ValueError as e:
                            raise ApiError(400, str(e))
                    if len(parts) == 3 and parts[0] == "agent-rules" and parts[1].isdigit() and parts[2] == "remove":
                        from .audit import remove_rule
                        try:
                            return self._send(200, {"ok": True, "message": remove_rule(db, int(parts[1]))})
                        except KeyError as e:
                            raise ApiError(404, str(e))
                    if parts == ["chat", "threads"]:
                        from .chat import new_thread
                        return self._send(200, {"id": new_thread(db)})
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
            etag = '"%s"' % hashlib.sha1(data).hexdigest()[:20]
            if etag in (self.headers.get("If-None-Match") or ""):
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            if len(data) > 20_000 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
                data, gz = _gzip_cached(p, data), True       # la carte (≈ 740 Ko) tombe à ≈ 120 Ko
            else:
                gz = False
            return self._send(200, data, ctype, gzipped=gz, etag=etag)

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


def library(db, query: str = "", item: int = 0):
    """Bibliothèque de recherches : liste (filtrée par mots-clés) ou contenu complet d'une entrée."""
    if item:
        r = db.one("SELECT * FROM web_cache WHERE id=?", (item,))
        if not r:
            raise ApiError(404, "Entrée introuvable")
        return dict(r)
    rows = db.cache_search(query, 60) if query.strip() else db.q("SELECT * FROM web_cache ORDER BY id DESC LIMIT 60")
    stats = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(kind='search'),0) AS s FROM web_cache")
    return {"total": stats["n"], "searches": stats["s"],
            "items": [{"id": r["id"], "kind": r["kind"], "key": r["key"].split("|", 1)[-1] if r["kind"] == "search" else r["key"],
                       "agent": r["agent"], "task_id": r["task_id"], "created_at": r["created_at"], "size": len(r["content"])} for r in rows]}


def _chat_history(db, thread: int = 0) -> dict:
    from .chat import history, latest_thread
    thread = thread or latest_thread(db)
    t = db.one("SELECT * FROM chat_threads WHERE id=?", (thread,)) if thread else None
    return {"thread": dict(t) if t else None, "messages": history(db, thread) if t else []}


def _chat_threads(db) -> list[dict]:
    from .chat import threads
    return threads(db)


def _chat_send(cfg, db, body: dict) -> dict:
    from .chat import send
    try:
        return send(cfg, db, body.get("text", ""), int(body.get("thread") or 0) or None)
    except ValueError as e:
        raise ApiError(400, str(e))


def make_server(cfg, port: int = 8765) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg))
    srv.daemon_threads = True
    return srv


def serve_in_thread(cfg, port: int = 8765) -> ThreadingHTTPServer:
    srv = make_server(cfg, port)
    threading.Thread(target=srv.serve_forever, name="ai-studio-http", daemon=True).start()
    return srv
