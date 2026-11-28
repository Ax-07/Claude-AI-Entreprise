"""Discussion en direct entre le propriétaire et le PDG, depuis le tableau de bord.

Le PDG répond tout de suite (appel synchrone au modèle fort). Il peut consulter l'état de l'entreprise et, si le
propriétaire le demande, lancer le travail : objectif pour une mission de fond, tâche pour une demande précise.
Il ne fait pas le travail lui-même dans la discussion : les agents s'en chargent au cycle suivant.
"""
from __future__ import annotations

import json

from .agents import ROLES, system_prompt
from .db import iso
from .llm import BudgetGuard, BudgetExceeded, LLMError, make_llm, models_for
from .tools import REGISTRY, ToolContext, run_tool

LLM_FACTORY = None          # remplacé par un faux modèle dans les tests
HISTORY = 16                # messages repris comme contexte (les plus récents)
MAX_STEPS = 6
TOOLS = ["create_objective", "delegate_task", "company_stats", "budget_status", "list_tasks", "get_task_result",
         "list_orders", "list_offers", "list_sales", "recall", "search_library"]

CHAT_RULES = """
Tu es en DISCUSSION DIRECTE avec le propriétaire, dans le tableau de bord. Règles de la discussion :
- Réponds comme un dirigeant à son associé : court, clair, concret, en français, en le tutoyant. Pas de longs rapports.
- Pour répondre sur l'état de l'entreprise, consulte les outils (company_stats, list_tasks, list_orders…) plutôt que d'inventer.
- Quand le propriétaire te confie une mission, lance-la : create_objective pour une mission de fond que tu découperas,
  delegate_task pour une demande précise à un agent. Dis ensuite en une phrase ce que tu as lancé et ce qui va se passer.
- Si la demande est ambiguë au point de risquer du travail inutile, pose UNE question courte avant de lancer quoi que ce soit.
- Tu ne fais pas le travail toi-même dans la discussion : les agents s'en chargent au prochain cycle du moteur.
- Les règles permanentes restent valables : toute action externe passe par la validation du propriétaire."""


def threads(db, limit: int = 50) -> list[dict]:
    """Discussions, la plus récente en premier, avec leur nombre de messages et leur coût."""
    rows = db.q("SELECT t.*, COUNT(c.id) AS messages, COALESCE(SUM(c.cost_usd),0) AS cost_usd FROM chat_threads t "
                "LEFT JOIN chat c ON c.thread_id=t.id GROUP BY t.id "
                "ORDER BY t.updated_at DESC, COUNT(c.id)=0 DESC, MAX(c.id) DESC, t.id DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in rows]


def new_thread(db, title: str = "Nouvelle discussion") -> int:
    return db.run("INSERT INTO chat_threads(title, created_at, updated_at) VALUES (?,?,?)", (title, iso(), iso()))


def latest_thread(db) -> int | None:
    t = threads(db, 1)
    return t[0]["id"] if t else None


def history(db, thread: int | None = None, limit: int = 200) -> list[dict]:
    thread = thread or latest_thread(db)
    if not thread:
        return []
    rows = db.q("SELECT id, role, text, cost_usd, created_at FROM chat WHERE thread_id=? ORDER BY id DESC LIMIT ?", (int(thread), int(limit)))
    return [dict(r) for r in reversed(rows)]


def _messages(db, thread: int) -> list[dict]:
    msgs = []
    for r in history(db, thread, HISTORY):
        role = "user" if r["role"] == "owner" else "assistant"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n\n" + r["text"]
        else:
            msgs.append({"role": role, "content": r["text"]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def send(cfg, db, text: str, thread: int | None = None) -> dict:
    """Enregistre le message du propriétaire, fait répondre le PDG et renvoie sa réponse."""
    text = " ".join(str(text or "").split()) if "\n" not in str(text or "") else str(text).strip()
    if not text:
        raise ValueError("Message vide.")
    if len(text) > 4000:
        raise ValueError("Message trop long (4 000 caractères au maximum).")
    if thread and not db.one("SELECT 1 FROM chat_threads WHERE id=?", (int(thread),)):
        raise ValueError("Discussion introuvable.")
    if not thread:
        thread = new_thread(db)
    thread = int(thread)
    if db.one("SELECT title FROM chat_threads WHERE id=?", (thread,))["title"] == "Nouvelle discussion":
        title = text.split("\n")[0]
        db.run("UPDATE chat_threads SET title=? WHERE id=?", (title[:60] + ("…" if len(title) > 60 else ""), thread))
    db.run("INSERT INTO chat(role, text, thread_id, created_at) VALUES ('owner', ?, ?, ?)", (text, thread, iso()))
    db.log("proprietaire", "chat", f"Message au PDG : {text[:150]}")
    role = ROLES["pdg"]
    guard = BudgetGuard(cfg, db)
    model = models_for(cfg)["strong"]
    ctx = ToolContext(cfg, db, "pdg", None, list(ROLES))
    system = [{"type": "text", "text": system_prompt(role, cfg) + "\n" + CHAT_RULES, "cache_control": {"type": "ephemeral"}}]
    tools = [REGISTRY[n].spec() for n in TOOLS if n in REGISTRY and REGISTRY[n].available()]
    messages, answer, cost, actions = _messages(db, thread), "", 0.0, []
    try:
        llm = (LLM_FACTORY or make_llm)(cfg)
        for step in range(MAX_STEPS):
            guard.check(None)
            resp = llm.complete(model, system, messages, tools, int(cfg["runtime"]["max_output_tokens"]))
            cost += guard.record("pdg", None, model, resp.get("usage", {}))
            content = resp.get("content", [])
            messages.append({"role": "assistant", "content": content})
            texts = [b.get("text", "") for b in content if b.get("type") == "text" and b.get("text")]
            if texts:
                answer = "\n".join(texts).strip()
            calls = [b for b in content if b.get("type") == "tool_use"]
            if not calls:
                break
            results = []
            for c in calls:
                out = run_tool(c["name"], ctx, c.get("input") or {})
                db.log("pdg", "tool", f"[discussion] {c['name']}({json.dumps(c.get('input') or {}, ensure_ascii=False)[:200]}) → {out[:200]}")
                if c["name"] in ("create_objective", "delegate_task"):
                    actions.append(out)
                results.append({"type": "tool_result", "tool_use_id": c["id"], "content": out})
            messages.append({"role": "user", "content": results})
        else:
            answer = answer or "Je n'ai pas pu terminer ma réponse : peux-tu reformuler plus simplement ?"
    except BudgetExceeded as e:
        answer = f"Je ne peux pas répondre : budget atteint ({e})."
    except LLMError as e:
        answer = f"Je ne peux pas répondre pour l'instant (erreur du modèle : {e})."
    answer = answer or ("C'est lancé." if actions else "(pas de réponse)")
    mid = db.run("INSERT INTO chat(role, text, cost_usd, thread_id, created_at) VALUES ('pdg', ?, ?, ?, ?)", (answer, round(cost, 6), thread, iso()))
    db.run("UPDATE chat_threads SET updated_at=? WHERE id=?", (iso(), thread))
    return {"id": mid, "role": "pdg", "text": answer, "cost_usd": round(cost, 6), "actions": actions, "thread": thread}
