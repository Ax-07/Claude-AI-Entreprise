"""Outils communs : fin de tâche, mémoire partagée, délégation, fichiers, lecture web, validations."""
from __future__ import annotations

import html
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from . import tool, ToolContext


# ---------------------------------------------------------------- tâche
@tool("complete_task",
      "Termine la tâche en cours avec un résultat clair et exploitable (livrable, conclusions, prochaines étapes). "
      "Appelle cet outil une seule fois, quand le travail est fini.",
      {"result": {"type": "string", "description": "Résultat complet de la tâche."}},
      ["result"])
def complete_task(ctx: ToolContext, result: str) -> str:
    ctx.finished = True
    ctx.result = result
    return "Tâche marquée comme terminée."


@tool("delegate_task",
      "Crée une tâche pour un autre agent (ou pour toi-même plus tard). Utilise-la pour découper le travail. "
      "Sois précis : objectif, contexte, livrable attendu.",
      {"agent": {"type": "string", "description": "Nom de l'agent destinataire."},
       "title": {"type": "string"},
       "description": {"type": "string", "description": "Contexte et livrable attendu."},
       "priority": {"type": "integer", "description": "1 = urgent … 5 = faible", "default": 3}},
      ["agent", "title", "description"])
def delegate_task(ctx: ToolContext, agent: str, title: str, description: str, priority: int = 3) -> str:
    agent = agent.strip().lower()
    if agent not in ctx.agent_names:
        return f"Agent inconnu. Agents disponibles : {', '.join(ctx.agent_names)}"
    rt = ctx.cfg["runtime"]
    depth = int(ctx.task["depth"]) + 1 if ctx.task is not None else 0
    if depth > rt["max_delegation_depth"]:
        return "Refusé : profondeur de délégation maximale atteinte. Fais le travail toi-même ou termine la tâche."
    if ctx.db.open_task_count() >= rt["max_open_tasks"]:
        return "Refusé : trop de tâches ouvertes. Termine d'abord les tâches en cours."
    tid = ctx.db.add_task(title, agent, description, priority,
                          parent_id=ctx.task["id"] if ctx.task is not None else None,
                          objective_id=ctx.task["objective_id"] if ctx.task is not None else None,
                          created_by=ctx.agent, depth=depth)
    ctx.db.log(ctx.agent, "delegation", f"Tâche #{tid} confiée à {agent} : {title}", tid)
    return f"Tâche #{tid} créée pour {agent}."


@tool("create_objective",
      "Enregistre un nouvel objectif de l'entreprise (mission de fond que le PDG découpera en tâches au prochain cycle). "
      "À n'utiliser que sur demande du propriétaire.",
      {"text": {"type": "string", "description": "l'objectif, formulé clairement avec le résultat attendu"},
       "activity": {"type": "string", "description": "activité concernée (services, video, ecommerce) ; vide si transverse"}},
      ["text"])
def create_objective(ctx: ToolContext, text: str, activity: str = "") -> str:
    from ..activities import exists
    text = " ".join(str(text).split())[:600]
    if len(text) < 8:
        return "Objectif trop vague : formule le résultat attendu."
    activity = activity.strip().lower() or None
    if activity and not exists(ctx.db, activity):
        return "Activité inconnue (services, video, ecommerce)."
    oid = ctx.db.add_objective(text, activity)
    ctx.db.log(ctx.agent, "objective", f"Nouvel objectif #{oid} (demandé par le propriétaire) : {text}")
    return f"Objectif #{oid} enregistré : il sera découpé en tâches au prochain cycle."


@tool("list_tasks", "Liste les tâches récentes (pour savoir ce qui est en cours ou terminé).",
      {"status": {"type": "string", "description": "pending, running, done, failed, blocked ou vide pour tout"},
       "agent": {"type": "string"}, "limit": {"type": "integer", "default": 15}})
def list_tasks(ctx: ToolContext, status: str = "", agent: str = "", limit: int = 15) -> str:
    sql, params = "SELECT id, agent, status, priority, title, substr(COALESCE(result,''),1,300) AS r FROM tasks WHERE 1=1", []
    if status:
        sql += " AND status=?"; params.append(status)
    if agent:
        sql += " AND agent=?"; params.append(agent.lower())
    sql += " ORDER BY id DESC LIMIT ?"; params.append(min(int(limit), 40))
    rows = ctx.db.q(sql, tuple(params))
    if not rows:
        return "Aucune tâche."
    return "\n".join(f"#{r['id']} [{r['status']}] ({r['agent']}, P{r['priority']}) {r['title']}" + (f" → {r['r']}" if r['r'] else "") for r in rows)


@tool("get_task_result", "Lit le résultat complet d'une tâche terminée.", {"task_id": {"type": "integer"}}, ["task_id"])
def get_task_result(ctx: ToolContext, task_id: int) -> str:
    t = ctx.db.task(int(task_id))
    if not t:
        return "Tâche introuvable."
    return f"#{t['id']} [{t['status']}] {t['title']} ({t['agent']})\n{t['result'] or '(pas encore de résultat)'}"


# ---------------------------------------------------------------- mémoire
@tool("remember",
      "Enregistre une information durable dans la mémoire partagée de l'entreprise (faits, décisions, sources, "
      "contacts, apprentissages). Un sujet court et précis.",
      {"topic": {"type": "string"}, "content": {"type": "string"}},
      ["topic", "content"])
def remember(ctx: ToolContext, topic: str, content: str) -> str:
    mid = ctx.db.remember(ctx.agent, topic, content)
    return f"Mémorisé (#{mid})."


@tool("recall", "Cherche dans la mémoire partagée de l'entreprise.", {"query": {"type": "string"}}, ["query"])
def recall(ctx: ToolContext, query: str) -> str:
    rows = ctx.db.recall(query, ctx.agent)
    if not rows:
        return "Rien trouvé en mémoire."
    return "\n".join(f"- [{r['topic']}] ({r['agent']}, {r['created_at'][:10]}) {r['content']}" for r in rows)


# ---------------------------------------------------------------- fichiers
def _safe_path(ctx: ToolContext, rel: str) -> Path:
    base = ctx.cfg.workspace.resolve()
    p = (base / rel.strip().lstrip("/\\")).resolve()
    if base != p and base not in p.parents:
        raise ValueError("chemin hors de l'espace de travail")
    return p


@tool("write_file",
      "Écrit un fichier texte (livrable, rapport, brouillon) dans l'espace de travail de l'entreprise. "
      "Pour un long document, écris-le en plusieurs appels d'environ 1 000 mots : le premier crée le fichier, "
      "les suivants ajoutent la suite avec append=true.",
      {"path": {"type": "string", "description": "ex. recherche/etude-marche.md"}, "content": {"type": "string"},
       "append": {"type": "boolean", "description": "true = ajoute à la fin du fichier existant", "default": False}},
      ["path", "content"])
def write_file(ctx: ToolContext, path: str, content: str, append: bool = False) -> str:
    p = _safe_path(ctx, path)
    size = (p.stat().st_size if (append and p.is_file()) else 0) + len(content.encode("utf-8"))
    if size > 200_000:
        return "Refusé : fichier trop gros (200 Ko max)."
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    ctx.db.log(ctx.agent, "file", f"Fichier {'complété' if append else 'écrit'} : {path}", ctx.task["id"] if ctx.task is not None else None)
    return f"Fichier {'complété' if append else 'enregistré'} : {path} ({size} octets)"


@tool("read_file", "Lit un fichier de l'espace de travail.", {"path": {"type": "string"}}, ["path"])
def read_file(ctx: ToolContext, path: str) -> str:
    p = _safe_path(ctx, path)
    if not p.is_file():
        return "Fichier introuvable."
    return p.read_text(encoding="utf-8", errors="replace")[:12000]


@tool("list_files", "Liste les fichiers de l'espace de travail.", {"folder": {"type": "string", "default": ""}})
def list_files(ctx: ToolContext, folder: str = "") -> str:
    p = _safe_path(ctx, folder or ".")
    if not p.is_dir():
        return "Dossier introuvable."
    base = ctx.cfg.workspace.resolve()
    files = [str(f.relative_to(base)) for f in sorted(p.rglob("*")) if f.is_file()][:100]
    return "\n".join(files) or "Aucun fichier."


# ---------------------------------------------------------------- web
class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0; self.title = ""; self._in_title = False
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"): self.skip += 1
        if tag == "title": self._in_title = True
        if tag in ("p", "br", "li", "h1", "h2", "h3", "h4", "tr", "div"): self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip: self.skip -= 1
        if tag == "title": self._in_title = False
    def handle_data(self, data):
        if self._in_title: self.title += data
        if not self.skip: self.parts.append(data)


def _public_host(host: str) -> bool:
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except Exception:
        return False


@tool("fetch_url", "Télécharge une page web publique et renvoie son texte (sans coût de recherche).",
      {"url": {"type": "string"}}, ["url"])
def fetch_url(ctx: ToolContext, url: str) -> str:
    url = url.strip().split("#")[0]
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return "URL invalide (http/https uniquement)."
    if not _public_host(u.hostname):
        return "Refusé : adresse non publique."
    hit = ctx.db.cache_get("page", url, PAGE_CACHE_DAYS)
    if hit:
        return f"[Page lue le {hit['created_at'][:10]} par {hit['agent']}, reprise de la bibliothèque sans nouveau téléchargement]\n" + hit["content"]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AI-Enterprise-Studio research bot)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        ctype = r.headers.get("content-type", "")
        raw = r.read(400_000)
    if "html" not in ctype and "text" not in ctype and "json" not in ctype:
        return f"Contenu non textuel ({ctype})."
    text = raw.decode("utf-8", "replace")
    if "html" in ctype:
        p = _Text(); p.feed(text)
        body = re.sub(r"\n\s*\n+", "\n\n", html.unescape("".join(p.parts)))
        body = re.sub(r"[ \t]+", " ", body).strip()
        text = (f"Titre : {p.title.strip()}\n\n" if p.title.strip() else "") + body
    text = text[:9000]
    if len(text.strip()) > 40:
        ctx.db.cache_put("page", url, ctx.agent, ctx.task["id"] if ctx.task is not None else None, text)
    return text


BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
SEARCH_CACHE_DAYS, PAGE_CACHE_DAYS = 7, 3     # une même recherche (ou page) récente est resservie sans coût


def brave_request(params: dict) -> dict:
    """Appel brut à l'API Brave Search (remplacé par un faux dans les tests)."""
    import json, os
    req = urllib.request.Request(BRAVE_URL + "?" + urllib.parse.urlencode(params),
                                 headers={"Accept": "application/json",
                                          "X-Subscription-Token": os.environ.get("BRAVE_API_KEY", "")})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


@tool("brave_search",
      "Recherche sur le web (Brave Search). Renvoie titres, URL et extraits : cite les URL que tu utilises "
      "et ouvre les pages importantes avec fetch_url avant d'en tirer des chiffres. Requêtes courtes et précises.",
      {"query": {"type": "string", "description": "La requête, 2 à 8 mots."},
       "count": {"type": "integer", "description": "Nombre de résultats (1 à 10).", "default": 5},
       "country": {"type": "string", "description": "Code pays (FR, US, GB…), FR par défaut.", "default": "FR"}},
      ["query"])
def brave_search(ctx: ToolContext, query: str, count: int = 5, country: str = "FR") -> str:
    import os
    if not os.environ.get("BRAVE_API_KEY"):
        return "Recherche indisponible : BRAVE_API_KEY absente du fichier .env."
    country = (country or "FR").upper()[:2]
    key = country + "|" + " ".join(query.lower().split())[:300]
    hit = ctx.db.cache_get("search", key, SEARCH_CACHE_DAYS)
    if hit:
        return (f"[Recherche déjà faite le {hit['created_at'][:10]} par {hit['agent']} : résultats repris de la bibliothèque, "
                "sans coût ni quota]\n" + hit["content"])
    limit = int(ctx.cfg["budget"]["web_searches_per_day"])
    if ctx.db.searches_today() >= limit:
        return (f"Refusé : limite de {limit} recherches web par jour atteinte. Utilise search_library (recherches "
                "déjà faites), la mémoire ou fetch_url.")
    params = {"q": query[:300], "count": max(1, min(int(count or 5), 10)), "country": country,
              "search_lang": "fr" if country == "FR" else "en"}
    data = brave_request(params)
    price = float(ctx.cfg["prices"].get("brave_search_per_call", 0.005))
    ctx.db.add_cost(ctx.agent, ctx.task["id"] if ctx.task is not None else None, "brave-search", 0, 0, 1, price)
    results = (data.get("web") or {}).get("results") or []
    if not results:
        return "Aucun résultat."
    lines = []
    for i, r in enumerate(results, 1):
        extra = " ".join(_clean(x) for x in (r.get("extra_snippets") or [])[:2])
        lines.append(f"{i}. {_clean(r.get('title'))}\n   {r.get('url')}\n   {_clean(r.get('description'))} {extra}".rstrip())
    out = "\n".join(lines)[:6000]
    ctx.db.cache_put("search", key, ctx.agent, ctx.task["id"] if ctx.task is not None else None, out)
    return out


@tool("search_library",
      "Cherche dans la bibliothèque de l'entreprise : recherches web et pages déjà lues par les agents ces 30 derniers "
      "jours (gratuit). À consulter AVANT une nouvelle recherche web. Renvoie requête ou URL, date, agent et extrait.",
      {"query": {"type": "string", "description": "mots-clés (ex. boulangerie Lyon avis)"},
       "limit": {"type": "integer", "default": 8}}, ["query"])
def search_library(ctx: ToolContext, query: str, limit: int = 8) -> str:
    rows = ctx.db.cache_search(query, min(int(limit or 8), 15))
    if not rows:
        return "Rien dans la bibliothèque pour ces mots-clés : une recherche web est justifiée."
    words = [w for w in query.lower().split() if len(w) > 2] or [query.lower()]
    out = []
    for r in rows:
        c = r["content"]; i = max(0, min((c.lower().find(w) for w in words if c.lower().find(w) >= 0), default=0) - 120)
        label = ("Recherche « " + r["key"].split("|", 1)[-1] + " »") if r["kind"] == "search" else "Page " + r["key"]
        out.append(f"- {label} ({r['created_at'][:10]}, {r['agent']}) : …{' '.join(c[i:i + 400].split())}…")
    return "\n".join(out) + "\n(Relance brave_search ou fetch_url avec la même requête ou URL pour le texte complet : il sera resservi sans coût.)"


# ---------------------------------------------------------------- propriétaire
@tool("request_owner_decision",
      "Demande une décision au propriétaire (humain) quand une action est risquée, coûteuse, engage l'entreprise "
      "ou sort de ton périmètre. Le propriétaire répondra plus tard ; continue ou termine ta tâche en attendant. "
      "Jamais pour faire valider un email, une page ou un lien de paiement (send_email, propose_page et "
      "propose_payment_link s'en chargent), ni pour demander au propriétaire de faire une action à ta place.",
      {"question": {"type": "string"}, "context": {"type": "string"}},
      ["question"])
def request_owner_decision(ctx: ToolContext, question: str, context: str = "") -> str:
    aid = ctx.db.add_approval(ctx.agent, ctx.task["id"] if ctx.task is not None else None,
                              "decision", {"question": question, "context": context}, question)
    ctx.db.log(ctx.agent, "approval", f"Décision demandée (#{aid}) : {question}")
    return f"Demande #{aid} envoyée au propriétaire. Tu seras recontacté via une nouvelle tâche quand il aura répondu."


@tool("list_objectives", "Liste les objectifs de l'entreprise fixés par le propriétaire.", {})
def list_objectives(ctx: ToolContext) -> str:
    rows = ctx.db.q("SELECT * FROM objectives ORDER BY id")
    return "\n".join(f"#{r['id']} [{r['status']}] {r['text']}" for r in rows) or "Aucun objectif."


@tool("set_objective_status",
      "Marque un objectif comme atteint (done) ou en pause (paused) avec une justification factuelle.",
      {"objective_id": {"type": "integer"}, "status": {"type": "string", "enum": ["done", "paused", "active"]},
       "note": {"type": "string"}}, ["objective_id", "status", "note"])
def set_objective_status(ctx: ToolContext, objective_id: int, status: str, note: str) -> str:
    if status not in ("done", "paused", "active"):
        return "Statut invalide."
    ctx.db.run("UPDATE objectives SET status=? WHERE id=?", (status, int(objective_id)))
    ctx.db.log(ctx.agent, "objective", f"Objectif #{objective_id} → {status} : {note}")
    return "Objectif mis à jour."


@tool("budget_status", "Affiche les dépenses API du jour et du mois par rapport aux plafonds.", {})
def budget_status(ctx: ToolContext) -> str:
    b = ctx.cfg["budget"]
    return (f"Aujourd'hui : {ctx.db.spent('day'):.4f} $ / {b['daily_usd']} $\n"
            f"Ce mois : {ctx.db.spent('month'):.4f} $ / {b['monthly_usd']} $\n"
            f"Recherches web aujourd'hui : {ctx.db.searches_today()} / {b['web_searches_per_day']}")
