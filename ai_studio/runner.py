"""Exécution d'une tâche par un agent : boucle « réflexion → outils → résultat »."""
from __future__ import annotations

import json
import os

from .agents import ROLES, system_prompt
from .llm import BudgetExceeded, LLMError, models_for, resolve_provider
from .tools import REGISTRY, ToolContext, run_tool


def _model_for(cfg, role) -> str:
    m = models_for(cfg)
    return m["strong"] if role.model == "strong" else m["default"]


def _tools_for(cfg, role, guard) -> list[dict]:
    specs = [REGISTRY[n].spec() for n in role.all_tools() if n in REGISTRY and REGISTRY[n].available()]
    if role.web_search:
        left = guard.searches_left()
        if os.environ.get("BRAVE_API_KEY"):
            # Brave en priorité : moins cher, et disponible quel que soit le fournisseur LLM.
            if left > 0:
                specs.append(REGISTRY["brave_search"].spec())
            return specs
        if resolve_provider(cfg) != "anthropic":
            return specs                      # pas d'outil serveur hors Anthropic
        ws = cfg.get("server_tools", "web_search", default={}) or {}
        if ws.get("enabled") and left > 0:
            specs.append({"type": ws["type"], "name": "web_search", "max_uses": min(int(ws.get("max_uses", 3)), left)})
        wf = cfg.get("server_tools", "web_fetch", default={}) or {}
        if wf.get("enabled"):
            specs.append({"type": wf["type"], "name": "web_fetch", "max_uses": int(wf.get("max_uses", 3)),
                          "max_content_tokens": int(wf.get("max_content_tokens", 6000))})
    return specs


def compact_history(messages: list, keep_last: int = 2, max_chars: int = 600) -> int:
    """Raccourcit les anciens résultats d'outils (pages lues, résultats de recherche…) déjà exploités.

    À chaque étape, tout l'historique est renvoyé au modèle : sans compactage, une page web lue à
    l'étape 1 est facturée à chaque étape suivante. On garde en entier les `keep_last` derniers
    messages de résultats d'outils et on tronque les plus anciens. Retourne le nombre de caractères gagnés.
    """
    result_msgs = [m for m in messages if m["role"] == "user" and isinstance(m["content"], list)
                   and any(b.get("type") == "tool_result" for b in m["content"])]
    saved = 0
    for m in result_msgs[:-keep_last] if keep_last else result_msgs:
        for b in m["content"]:
            c = b.get("content")
            if b.get("type") == "tool_result" and isinstance(c, str) and len(c) > max_chars and not b.get("_compacte"):
                cut = c[: max_chars // 2]
                b["content"] = cut + f"\n[… résultat ancien raccourci ({len(c)} caractères) pour économiser des tokens ; " \
                                     "ce qui comptait doit être dans tes notes, ta mémoire ou tes fichiers.]"
                b["_compacte"] = True
                saved += len(c) - len(b["content"])
    return saved


def _clean_for_api(messages: list) -> list:
    """Retire les marqueurs internes avant l'envoi à l'API."""
    out = []
    for m in messages:
        if isinstance(m["content"], list):
            m = {**m, "content": [{k: v for k, v in b.items() if not k.startswith("_")} for b in m["content"]]}
        out.append(m)
    return out


def _briefing(db, task) -> str:
    parts = [f"Tâche #{task['id']} : {task['title']}", "", task["description"] or "(pas de description)"]
    if task["parent_id"]:
        p = db.task(task["parent_id"])
        if p:
            parts += ["", f"Contexte — tâche parente #{p['id']} ({p['agent']}) : {p['title']}"]
    if task["objective_id"]:
        o = db.one("SELECT text FROM objectives WHERE id=?", (task["objective_id"],))
        if o:
            parts += [f"Objectif de l'entreprise concerné : {o['text']}"]
    mem = db.recall(task["title"], task["agent"], limit=5)
    if mem:
        parts += ["", "Éléments de mémoire possiblement utiles :"] + [f"- [{m['topic']}] {m['content'][:300]}" for m in mem]
    return "\n".join(parts)


def run_task(cfg, db, llm, guard, task) -> str:
    """Exécute une tâche. Retourne le statut final. Lève BudgetExceeded si le budget jour/mois est épuisé."""
    role = ROLES.get(task["agent"])
    if not role:
        db.set_task(task["id"], status="failed", result=f"Agent inconnu : {task['agent']}")
        return "failed"
    rt = cfg["runtime"]
    model = _model_for(cfg, role)
    db.set_task(task["id"], status="running", attempts=task["attempts"] + 1)
    db.log(role.name, "start", f"Début : {task['title']}", task["id"])

    ctx = ToolContext(cfg, db, role.name, task, list(ROLES.keys()))
    system = [{"type": "text", "text": system_prompt(role, cfg), "cache_control": {"type": "ephemeral"}}]
    messages: list[dict] = [{"role": "user", "content": _briefing(db, task)}]
    last_text = ""
    max_steps = int(role.max_steps or rt["max_steps_per_task"])

    try:
        for step in range(max_steps + 1):
            if step == max_steps:
                note = {"type": "text", "text": "Limite d'étapes atteinte. Appelle maintenant complete_task avec le meilleur résultat possible."}
                if messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], list):
                    messages[-1]["content"].append(note)
                else:
                    messages.append({"role": "user", "content": [note]})
            guard.check(task["id"])
            compact_history(messages, int(rt.get("keep_full_tool_results", 2)), int(rt.get("old_tool_result_chars", 600)))
            resp = llm.complete(model, system, _clean_for_api(messages), _tools_for(cfg, role, guard), int(rt["max_output_tokens"]))
            guard.record(role.name, task["id"], model, resp.get("usage", {}))
            content = resp.get("content", [])
            messages.append({"role": "assistant", "content": content})
            texts = [b.get("text", "") for b in content if b.get("type") == "text"]
            if texts:
                last_text = "\n".join(t for t in texts if t).strip() or last_text

            if resp.get("stop_reason") == "pause_turn":       # outil serveur (recherche web) encore en cours
                continue
            calls = [b for b in content if b.get("type") == "tool_use"]
            if not calls:
                ctx.finished, ctx.result = True, last_text or "(aucun résultat)"
                break
            results = []
            for c in calls:
                out = run_tool(c["name"], ctx, c.get("input") or {})
                db.log(role.name, "tool", f"{c['name']}({json.dumps(c.get('input') or {}, ensure_ascii=False)[:200]}) → {out[:200]}", task["id"])
                results.append({"type": "tool_result", "tool_use_id": c["id"], "content": out})
            messages.append({"role": "user", "content": results})
            if ctx.finished:
                break
    except BudgetExceeded as e:
        cost = db.task_cost(task["id"])
        if "par tâche" in str(e):
            db.set_task(task["id"], status="done", cost_usd=cost,
                        result=(last_text or "") + f"\n\n[Arrêt : {e}. Résultat partiel.]")
            db.log(role.name, "budget", f"Tâche #{task['id']} arrêtée : {e}", task["id"])
            return "done"
        db.set_task(task["id"], status="pending", cost_usd=cost, attempts=task["attempts"])
        raise
    except LLMError as e:
        cost = db.task_cost(task["id"])
        if e.fatal:
            # Problème de configuration (modèle, clé, paramètre) : la tâche n'y est pour rien, elle reste en attente.
            db.set_task(task["id"], status="pending", cost_usd=cost, attempts=task["attempts"])
            db.log(role.name, "error", f"Configuration à corriger — tâche #{task['id']} remise en attente : {e}", task["id"])
            raise
        status = "failed" if task["attempts"] + 1 >= int(rt["max_attempts"]) else "pending"
        db.set_task(task["id"], status=status, cost_usd=cost, result=f"Erreur API : {e}")
        db.log(role.name, "error", f"Tâche #{task['id']} : {e}", task["id"])
        return status

    cost = db.task_cost(task["id"])
    result = ctx.result or last_text or "(terminé sans résultat explicite)"
    db.set_task(task["id"], status="done", result=result, cost_usd=cost)
    db.log(role.name, "done", f"Terminé : {task['title']} ({cost:.4f} $)", task["id"])
    return "done"
