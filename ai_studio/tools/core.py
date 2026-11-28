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
from ..db import iso


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
    if agent == "rh":
        return ("Refusé : l'auditeur RH est indépendant, il ne reçoit pas de tâches des agents. Il audite chaque semaine "
                "le travail de toute l'équipe et rend compte au propriétaire.")
    from ..missions import has_pm, mission_of, PM
    m = mission_of(ctx.task)
    if m and has_pm(ctx.db, *m) and ctx.agent not in (PM, "pdg"):
        return ("Refusé : cette mission est pilotée par un chef de projet, c'est lui qui distribue le travail. Ne lui "
                "transmets rien par tâche : il est réveillé automatiquement à la fin de la tienne. Termine ta tâche avec "
                "complete_task : ton résultat, le fichier produit, et ta recommandation pour l'étape suivante.")
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


@tool("launch_mission",
      "Lance une NOUVELLE mission demandée par le propriétaire : elle reçoit son numéro, apparaît dans le tableau des "
      "missions et toutes ses tâches lui sont rattachées. Mission simple (une étape, un agent) : indique l'agent et la "
      "consigne. Mission de plusieurs étapes ou agents : project_manager=true, un chef de projet la pilotera.",
      {"title": {"type": "string", "description": "intitulé court de la mission (ce qu'on veut obtenir)"},
       "instructions": {"type": "string", "description": "demande détaillée : contexte, livrable attendu, contraintes"},
       "agent": {"type": "string", "description": "mission simple : agent qui la réalise"},
       "project_manager": {"type": "boolean", "description": "true pour une mission de plusieurs étapes ou agents"},
       "activity": {"type": "string", "description": "services, video, ecommerce ; vide si transverse"},
       "priority": {"type": "integer", "default": 2}},
      ["title", "instructions"])
def launch_mission(ctx: ToolContext, title: str, instructions: str, agent: str = "", project_manager: bool = False,
                   activity: str = "", priority: int = 2) -> str:
    from ..activities import exists
    from ..missions import assign_pm
    title, instructions = " ".join(title.split())[:150], instructions.strip()[:3000]
    if len(title) < 5:
        return "Intitulé trop vague."
    agent = agent.strip().lower()
    if not project_manager and agent not in ctx.agent_names:
        return "Précise l'agent qui réalise la mission (ou project_manager=true pour une mission de plusieurs étapes)."
    activity = activity.strip().lower() or None
    if activity and not exists(ctx.db, activity):
        return "Activité inconnue (services, video, ecommerce)."
    oid = ctx.db.add_objective(f"{title} — {instructions}" if instructions else title, activity)
    ctx.db.log(ctx.agent, "objective", f"Mission #{oid} lancée : {title}")
    if project_manager:
        assign_pm(ctx.db, "objectif", oid, instructions, ctx.agent)
        return f"Mission #{oid} lancée et confiée à un chef de projet."
    ctx.db.run("UPDATE objectives SET simple=1 WHERE id=?", (oid,))
    tid = ctx.db.add_task(title, agent, instructions + "\n\nCette tâche est à elle seule la mission : quand elle est faite, "
                          "termine-la avec un résultat clair ; la mission passera dans « À vérifier » pour le propriétaire. "
                          "Si la mémoire ou les emails montrent que la demande a DÉJÀ été traitée, ne la refais pas en double : "
                          "dis précisément ce qui a été fait, quand, avec quel résultat, et ce que tu recommandes.",
                          max(1, min(5, int(priority or 2))), objective_id=oid, created_by=ctx.agent)
    ctx.db.log(ctx.agent, "delegation", f"Mission #{oid} : tâche #{tid} confiée à {agent}", tid)
    return f"Mission #{oid} lancée : tâche #{tid} confiée à {agent}."


@tool("assign_project_manager",
      "Confie une mission (objectif ou commande) à un chef de projet qui la pilotera jusqu'au bout : planification, "
      "délégation, suivi de chaque étape, contrôle et clôture. Pour toute mission de plusieurs étapes ou agents. "
      "Si la mission a déjà un chef de projet et qu'elle est à l'arrêt, il est relancé avec tes consignes (brief).",
      {"kind": {"type": "string", "enum": ["objectif", "commande"]}, "id": {"type": "integer", "description": "n° de l'objectif ou de la commande"},
       "brief": {"type": "string", "description": "consignes de lancement : priorités, contraintes, livrable attendu"}}, ["kind", "id"])
def assign_project_manager(ctx: ToolContext, kind: str, id: int, brief: str = "") -> str:
    from ..missions import assign_pm, has_pm, detail
    try:
        detail(ctx.db, kind, int(id))
    except KeyError:
        return "Mission introuvable."
    if has_pm(ctx.db, kind, int(id)):
        from ..missions import relaunch_mission
        tid = relaunch_mission(ctx.db, kind, int(id), brief)
        if tid:
            return f"Cette mission avait déjà un chef de projet : il est relancé (tâche #{tid}) avec ta consigne."
        return ("Cette mission a déjà un chef de projet et du travail est en cours ou attend une validation du "
                "propriétaire : rien à relancer pour l'instant (mission_status pour le détail).")
    tid = assign_pm(ctx.db, kind, int(id), brief, ctx.agent)
    return f"Chef de projet nommé (tâche de lancement #{tid}). Il te remontera ce qui dépasse sa mission."


@tool("mission_status", "Dossier complet d'une mission : demande, consignes du propriétaire, anomalies, tâches et résultats. "
      "Sans paramètre : la mission de ta tâche en cours.",
      {"kind": {"type": "string", "enum": ["objectif", "commande"]}, "id": {"type": "integer"}})
def mission_status(ctx: ToolContext, kind: str = "", id: int = 0) -> str:
    from ..missions import dossier, mission_of
    m = (kind, int(id)) if kind and id else mission_of(ctx.task)
    if not m:
        return "Aucune mission rattachée à cette tâche : précise kind et id."
    try:
        return dossier(ctx.db, *m)[:9000]
    except KeyError:
        return "Mission introuvable."


@tool("close_mission", "Clôture la mission de ta tâche quand le résultat répond vraiment à la demande, avec un bilan pour "
      "le propriétaire (ce qui a été produit, où le trouver, ce qui reste éventuellement). Il vérifiera ensuite.",
      {"summary": {"type": "string"}}, ["summary"])
def close_mission(ctx: ToolContext, summary: str) -> str:
    from ..missions import close, mission_of
    m = mission_of(ctx.task)
    if not m:
        return "Aucune mission rattachée à cette tâche."
    if len(summary.strip()) < 30:
        return "Bilan trop court : dis ce qui a été produit et où le trouver."
    return close(ctx.db, m[0], m[1], summary.strip(), ctx.agent)


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


@tool("read_file", "Lit un fichier de l'espace de travail (12 000 caractères à la fois ; offset pour lire la suite).",
      {"path": {"type": "string"}, "offset": {"type": "integer", "description": "position de départ (0 par défaut)", "default": 0}}, ["path"])
def read_file(ctx: ToolContext, path: str, offset: int = 0) -> str:
    p = _safe_path(ctx, path)
    if not p.is_file():
        return "Fichier introuvable."
    text = p.read_text(encoding="utf-8", errors="replace")
    offset = max(0, int(offset or 0))
    part = text[offset:offset + 12000]
    if offset + 12000 < len(text):
        part += f"\n\n[… suite non affichée : {len(text) - offset - 12000} caractères. read_file avec offset={offset + 12000}]"
    return part


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


@tool("seo_check",
      "Analyse SEO technique d'un site (gratuite, sans recherche web) : title, meta description, titres, mobile, HTTPS, "
      "robots.txt, sitemap, données structurées d'entreprise, téléphones et cohérence, liens cassés, mentions légales, "
      "sur l'accueil et quelques pages internes. Renvoie des faits MESURÉS avec leur source et un score technique, et "
      "enregistre l'analyse dans analyses/. Utilise-la au lieu de lire le code des pages toi-même.",
      {"url": {"type": "string", "description": "adresse du site (accueil)"},
       "max_pages": {"type": "integer", "description": "pages analysées, accueil compris (1 à 8, 5 par défaut)"}}, ["url"])
def seo_check(ctx: ToolContext, url: str, max_pages: int = 5) -> str:
    from .. import seo
    url = url.strip()
    key = re.sub(r"^https?://", "", url).rstrip("/").lower()
    hit = ctx.db.cache_get("seo", key, 1)
    if hit:
        return f"[Analyse du {hit['created_at'][:16]} par {hit['agent']}, reprise sans nouveau téléchargement]\n" + hit["content"]
    res = seo.check_site(url, max(1, min(8, int(max_pages or 5))))
    out = seo.report(res)
    host = re.sub(r"[^a-z0-9.-]", "", key.split("/")[0])[:60] or "site"
    rel = f"analyses/seo-{host}-{res['checked_at'][:10]}.md"
    f = ctx.cfg.workspace / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(out + "\n", encoding="utf-8")
    if res.get("home_status") == 200:
        ctx.db.cache_put("seo", key, ctx.agent, ctx.task["id"] if ctx.task is not None else None, out)
    ctx.db.log(ctx.agent, "file", f"Analyse SEO de {url} : score {res.get('score', '—')}/100 → {rel}")
    return out + f"\n\n(Analyse enregistrée dans {rel}.)"


@tool("agent_audit", "Dossier chiffré du travail des agents sur N jours (réussite, coûts, doublons, délégations, mémoire, "
      "demandes au propriétaire acceptées/refusées/corrigées, pilotage des missions, consignes en vigueur). Gratuit.",
      {"days": {"type": "integer", "description": "période en jours (7 par défaut, 90 au plus)"}})
def agent_audit(ctx: ToolContext, days: int = 7) -> str:
    from ..audit import dossier, to_markdown
    return to_markdown(dossier(ctx.cfg, ctx.db, max(1, min(90, int(days or 7)))))


@tool("propose_agent_rule",
      "Propose au propriétaire une consigne permanente pour un agent (appliquée à toutes ses tâches après validation). "
      "Une phrase courte, précise, vérifiable, appuyée sur une preuve chiffrée du dossier.",
      {"agent": {"type": "string"}, "rule": {"type": "string", "description": "la consigne, à l'impératif, 300 caractères au plus"},
       "evidence": {"type": "string", "description": "la preuve : chiffres du dossier, numéros de tâches"}},
      ["agent", "rule", "evidence"])
def propose_agent_rule(ctx: ToolContext, agent: str, rule: str, evidence: str) -> str:
    agent, rule, evidence = agent.strip().lower(), " ".join(rule.split()), evidence.strip()
    if agent not in ctx.agent_names:
        return f"Agent inconnu. Agents : {', '.join(ctx.agent_names)}"
    if not 15 <= len(rule) <= 300:
        return "Consigne de 15 à 300 caractères."
    if len(evidence) < 20:
        return "Donne une preuve chiffrée (chiffres du dossier, numéros de tâches)."
    if re.search(r"(?i)sans (validation|l'accord|demander)|ignore|contourne|ne (demande|soumets) (pas|plus)|envoie directement", rule):
        return "Refusé : une consigne ne peut pas contourner les validations du propriétaire ni les règles existantes."
    from ..audit import rules_for
    cur = rules_for(ctx.db, agent)
    if len(cur) >= int(ctx.cfg.get("audit", "max_rules_per_agent", default=8)):
        return f"{agent} a déjà {len(cur)} consignes : propose plutôt d'en remplacer une (dis laquelle dans evidence)."
    if any(rule.lower() == r.lower() for r in cur):
        return "Cette consigne existe déjà."
    if ctx.db.one("SELECT 1 FROM approvals WHERE action='agent_rule' AND status='pending' AND json_extract(payload,'$.agent')=? "
                  "AND json_extract(payload,'$.rule')=?", (agent, rule)):
        return "Cette consigne attend déjà la décision du propriétaire."
    aid = ctx.db.add_approval(ctx.agent, ctx.task["id"] if ctx.task is not None else None, "agent_rule",
                              {"agent": agent, "rule": rule, "evidence": evidence[:800], "current": cur},
                              f"Consigne permanente proposée pour {agent}")
    ctx.db.log(ctx.agent, "rh", f"Consigne proposée pour {agent} (#{aid}) : {rule[:100]}")
    return f"Consigne #{aid} soumise au propriétaire : elle s'appliquera à toutes les tâches de {agent} s'il la valide."


@tool("list_agent_rules", "Consignes permanentes en vigueur, par agent.", {"agent": {"type": "string"}})
def list_agent_rules(ctx: ToolContext, agent: str = "") -> str:
    rows = ctx.db.q("SELECT id, agent, rule, created_at FROM agent_rules WHERE active=1" + (" AND agent=?" if agent else "")
                    + " ORDER BY agent, id", ((agent.strip().lower(),) if agent else ()))
    return "\n".join(f"#{r['id']} {r['agent']} (depuis le {r['created_at'][:10]}) : {r['rule']}" for r in rows) or "Aucune consigne permanente."


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
    ctx.db.run("UPDATE objectives SET status=?, done_at=CASE WHEN ?='done' THEN ? ELSE done_at END, "
               "owner_check=CASE WHEN ?='done' THEN NULL ELSE owner_check END WHERE id=?",
               (status, status, iso(), status, int(objective_id)))
    ctx.db.log(ctx.agent, "objective", f"Objectif #{objective_id} → {status} : {note}")
    return "Objectif mis à jour."


@tool("budget_status", "Affiche les dépenses API du jour et du mois par rapport aux plafonds.", {})
def budget_status(ctx: ToolContext) -> str:
    b = ctx.cfg["budget"]
    return (f"Aujourd'hui : {ctx.db.spent('day'):.4f} $ / {b['daily_usd']} $\n"
            f"Ce mois : {ctx.db.spent('month'):.4f} $ / {b['monthly_usd']} $\n"
            f"Recherches web aujourd'hui : {ctx.db.searches_today()} / {b['web_searches_per_day']}")
