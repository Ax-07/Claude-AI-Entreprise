"""Clients LLM (Anthropic et OpenAI, sans dépendance externe) + calcul des coûts + plafonds budgétaires.

Le moteur parle en interne le format « Messages » d'Anthropic (blocs text / tool_use / tool_result).
Le client OpenAI traduit ce format vers l'API Chat Completions et retraduit la réponse : le reste du
moteur (runner, outils, tests) n'a donc pas à savoir quel fournisseur est utilisé.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def fatal(self) -> bool:
        """Erreur de configuration (requête refusée, clé, modèle) : réessayer ne sert à rien."""
        return self.status in (400, 401, 403, 404)


class BudgetExceeded(Exception):
    """Levée quand un plafond (jour, mois, tâche, recherches) est atteint : le travail s'arrête proprement."""


def _http_post_json(url: str, body: dict, headers: dict, timeout: int) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_with_retries(url: str, body: dict, headers: dict, timeout: int, transport=None) -> dict:
    """POST JSON avec 3 nouvelles tentatives sur les erreurs temporaires (429, 5xx, réseau)."""
    send = transport or _http_post_json
    delay = 5
    for attempt in range(4):
        try:
            return send(url, body, headers, timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:800]
            if e.code in (429, 500, 502, 503, 529) and attempt < 3:
                time.sleep(delay); delay *= 2
                continue
            raise LLMError(f"API {e.code} : {detail}", status=e.code) from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(delay); delay *= 2
                continue
            raise LLMError(f"Réseau : {e}") from None
    raise LLMError("Échec après plusieurs tentatives")


# ====================================================================== Anthropic
class AnthropicClient:
    provider = "anthropic"

    def __init__(self, api_key: str | None = None, timeout: int = 120, transport=None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.timeout, self.transport = timeout, transport
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY manquante : ajoute-la dans le fichier .env")

    def complete(self, model: str, system: list | str, messages: list, tools: list | None = None,
                 max_tokens: int = 2000) -> dict:
        body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            body["tools"] = tools
        headers = {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION}
        return _post_with_retries(ANTHROPIC_URL, body, headers, self.timeout, self.transport)


# ====================================================================== OpenAI
def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if b.get("type") == "text")


def to_openai_messages(system, messages: list) -> list:
    """Format Anthropic → messages Chat Completions."""
    out = []
    sys_text = _text_of(system) if not isinstance(system, str) else system
    if sys_text:
        out.append({"role": "system", "content": sys_text})
    for m in messages:
        content = m["content"]
        if m["role"] == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content}); continue
            texts = []
            for b in content:
                if b.get("type") == "tool_result":
                    c = b.get("content", "")
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                "content": c if isinstance(c, str) else _text_of(c)})
                elif b.get("type") == "text":
                    texts.append(b.get("text", ""))
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
        else:  # assistant
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content}); continue
            text = _text_of(content)
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                     for b in content if b.get("type") == "tool_use"]
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
    return out


def to_openai_tools(tools: list | None) -> list:
    """Outils locaux uniquement : les outils serveur d'Anthropic (web_search…) n'existent pas chez OpenAI."""
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t["input_schema"]}}
            for t in tools or [] if "input_schema" in t]


def from_openai_response(resp: dict) -> dict:
    """Réponse Chat Completions → format Anthropic attendu par le runner."""
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for c in msg.get("tool_calls") or []:
        fn = c.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"_arguments_invalides": fn.get("arguments", "")}
        content.append({"type": "tool_use", "id": c["id"], "name": fn.get("name", ""), "input": args})
    finish = choice.get("finish_reason")
    stop = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}.get(finish, "end_turn")
    if any(b["type"] == "tool_use" for b in content):
        stop = "tool_use"
    u = resp.get("usage") or {}
    cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
    usage = {"input_tokens": max(0, int(u.get("prompt_tokens", 0)) - cached),
             "cache_read_input_tokens": cached,
             "output_tokens": int(u.get("completion_tokens", 0))}
    return {"content": content, "stop_reason": stop, "usage": usage}


class OpenAIClient:
    provider = "openai"

    def __init__(self, api_key: str | None = None, timeout: int = 120, transport=None, base_url: str | None = None,
                 reasoning_effort: str | None = "none"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        # Les modèles à raisonnement refusent les outils dans Chat Completions si le raisonnement est actif :
        # on envoie "none" par défaut ; si le modèle ne connaît pas ce paramètre, on le retire automatiquement.
        self.reasoning_effort = reasoning_effort or None
        self.url = (base_url or os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
        self.url = f"{self.url}/chat/completions" if self.url else OPENAI_URL
        self.timeout, self.transport = timeout, transport
        if not self.api_key:
            raise LLMError("OPENAI_API_KEY manquante : ajoute-la dans le fichier .env")

    def complete(self, model: str, system: list | str, messages: list, tools: list | None = None,
                 max_tokens: int = 2000) -> dict:
        body = {"model": model, "messages": to_openai_messages(system, messages),
                "max_completion_tokens": max_tokens}
        oa_tools = to_openai_tools(tools)
        if oa_tools:
            body["tools"] = oa_tools
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        headers = {"authorization": f"Bearer {self.api_key}"}
        try:
            resp = _post_with_retries(self.url, body, headers, self.timeout, self.transport)
        except LLMError as e:
            msg = str(e).lower()
            if e.status == 400 and "reasoning_effort" in msg and ("unsupported" in msg or "not supported" in msg or "unrecognized" in msg) \
                    and "function tools" not in msg and "reasoning_effort" in body:
                self.reasoning_effort = None          # modèle sans raisonnement : on retire le paramètre
                body.pop("reasoning_effort")
                resp = _post_with_retries(self.url, body, headers, self.timeout, self.transport)
            else:
                raise
        return from_openai_response(resp)


# ====================================================================== choix du fournisseur
def resolve_provider(cfg) -> str:
    """'anthropic' | 'openai'. En mode 'auto', choisit selon la clé présente dans l'environnement (.env)."""
    p = (cfg.get("provider", default="auto") or "auto").lower()
    if p in ("anthropic", "openai"):
        return p
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "anthropic"


def models_for(cfg) -> dict:
    """{'default': …, 'strong': …} pour le fournisseur actif.

    Compatible avec l'ancien format de config.json (models = {"default", "strong"} = modèles Anthropic).
    """
    models = cfg["models"]
    provider = resolve_provider(cfg)
    if provider == "anthropic" and "default" in models:      # ancien format, prioritaire s'il est présent
        return {"default": models["default"], "strong": models.get("strong", models["default"])}
    if provider in models and isinstance(models[provider], dict):
        return models[provider]
    from .config import DEFAULTS
    return DEFAULTS["models"][provider]


def make_llm(cfg):
    timeout = int(cfg["runtime"]["request_timeout_seconds"])
    if resolve_provider(cfg) == "openai":
        return OpenAIClient(timeout=timeout, reasoning_effort=cfg.get("openai", "reasoning_effort", default="none"))
    return AnthropicClient(timeout=timeout)


# ====================================================================== coûts et budget
def compute_cost(prices: dict, model: str, usage: dict) -> tuple[float, int, int, int]:
    """Retourne (usd, tokens_entrée, tokens_sortie, recherches_web) pour une réponse (format Anthropic)."""
    p = prices.get(model) or prices.get("claude-sonnet-5-5") or {"input": 3.0, "output": 15.0}
    inp = int(usage.get("input_tokens", 0))
    cache_w = int(usage.get("cache_creation_input_tokens", 0) or 0)
    cache_r = int(usage.get("cache_read_input_tokens", 0) or 0)
    out = int(usage.get("output_tokens", 0))
    total_in = inp + cache_w + cache_r
    pin, pout = p["input"], p["output"]
    if "long_threshold" in p and total_in > p["long_threshold"]:
        pin, pout = p.get("long_input", pin), p.get("long_output", pout)
    pcache = p.get("cached_input", pin * 0.1)
    usd = (inp * pin + cache_w * pin * 1.25 + cache_r * pcache + out * pout) / 1_000_000
    searches = int((usage.get("server_tool_use") or {}).get("web_search_requests", 0) or 0)
    usd += searches * float(prices.get("web_search_per_call", 0.01))
    return round(usd, 6), total_in, out, searches


CHARS_PER_TOKEN = 3.0          # estimation prudente (le français compte plutôt 3,5 à 4 caractères par token)
MIN_OUTPUT_TOKENS = 1024       # en dessous, la réponse serait inutilisable : l'appel est refusé
RESERVATION_TTL_MIN = 15       # réservation d'un appel interrompu (programme arrêté) : ignorée après ce délai


def estimate_input_tokens(system, messages, tools) -> int:
    import json
    raw = json.dumps([system, messages, tools or []], ensure_ascii=False, default=str)
    return int(len(raw) / CHARS_PER_TOKEN) + 200


class BudgetGuard:
    """Plafonds de dépense (audit FIN-01).

    Avant chaque appel, le coût MAXIMAL possible (entrée estimée prudemment, sortie au maximum autorisé, recherches
    web) est RÉSERVÉ dans une transaction SQLite exclusive, en tenant compte des réservations en cours des autres
    appels (discussion avec le PDG et moteur en parallèle). Après la réponse, la réservation est remplacée par le coût
    réel. Les plafonds du jour et du mois sont donc stricts ; s'il reste trop peu pour la réponse maximale, la longueur
    de réponse autorisée est réduite pour tenir dans le budget. Le plafond par tâche reste « souple » : la tâche
    s'arrête avec un résultat partiel dès qu'il est atteint."""

    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db

    def _held(self, where: str = "", params: tuple = ()) -> float:
        from .db import iso, now
        from datetime import timedelta
        limit = iso(now() - timedelta(minutes=RESERVATION_TTL_MIN))
        return float(self.db.one(f"SELECT COALESCE(SUM(usd),0) FROM budget_reservations WHERE created_at >= ? {where}",
                                 (limit, *params))[0])

    def _task_cap(self, task_id) -> float:
        cap = self.cfg["budget"]["per_task_usd"]
        row = self.db.one("SELECT agent FROM tasks WHERE id=?", (task_id,))
        if row:
            from .agents import ROLES
            role = ROLES.get(row[0])
            cap = (role.task_budget_usd if role and role.task_budget_usd else cap)
        return cap

    def check(self, task_id: int | None = None) -> None:
        b = self.cfg["budget"]
        held = self._held()
        if self.db.spent("month") + held >= b["monthly_usd"]:
            raise BudgetExceeded(f"Budget mensuel atteint ({b['monthly_usd']} $)")
        if self.db.spent("day") + held >= b["daily_usd"]:
            raise BudgetExceeded(f"Budget du jour atteint ({b['daily_usd']} $)")
        if task_id is not None:
            cap = self._task_cap(task_id)
            if self.db.task_cost(task_id) + self._held("AND task_id=?", (task_id,)) >= cap:
                raise BudgetExceeded(f"Plafond par tâche atteint ({cap} $)")

    def reserve(self, agent: str, task_id, model: str, system, messages, tools, max_tokens: int) -> tuple[int, int]:
        """Réserve le coût maximal du prochain appel. Renvoie (id de réservation, max_tokens autorisé).
        Lève BudgetExceeded si le budget ne permet même pas une réponse courte."""
        from .db import iso, now
        from datetime import timedelta
        b, prices = self.cfg["budget"], self.cfg["prices"]
        in_tokens = estimate_input_tokens(system, messages, tools)
        searches = sum(int(t.get("max_uses", 0) or 0) for t in (tools or []) if isinstance(t, dict) and t.get("type", "").startswith("web_search"))
        fixed, *_ = compute_cost(prices, model, {"input_tokens": in_tokens, "output_tokens": 0,
                                                 "server_tool_use": {"web_search_requests": searches}})
        full, *_ = compute_cost(prices, model, {"input_tokens": in_tokens, "output_tokens": max_tokens,
                                                "server_tool_use": {"web_search_requests": searches}})
        per_out = (full - fixed) / max(1, max_tokens)
        conn = self.db.conn
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")           # une seule réservation à la fois, toutes connexions confondues
        try:
            conn.execute("DELETE FROM budget_reservations WHERE created_at < ?",
                         (iso(now() - timedelta(minutes=RESERVATION_TTL_MIN)),))
            held = self._held()
            day_left = b["daily_usd"] - self.db.spent("day") - held
            month_left = b["monthly_usd"] - self.db.spent("month") - held
            left = min(day_left, month_left)
            if task_id is not None:
                cap = self._task_cap(task_id)
                if self.db.task_cost(task_id) + self._held("AND task_id=?", (task_id,)) >= cap:
                    raise BudgetExceeded(f"Plafond par tâche atteint ({cap} $)")
            allowed = max_tokens
            if full > left:
                allowed = int((left - fixed) / per_out) if per_out > 0 else 0
                if allowed < MIN_OUTPUT_TOKENS:
                    raise BudgetExceeded(f"Budget mensuel atteint ({b['monthly_usd']} $)" if month_left < day_left
                                         else f"Budget du jour atteint ({b['daily_usd']} $)")
            usd = round(fixed + per_out * allowed, 6)
            cur = conn.execute("INSERT INTO budget_reservations(agent, task_id, model, usd, created_at) VALUES (?,?,?,?,?)",
                               (agent, task_id, model, usd, iso()))
            conn.commit()
            return cur.lastrowid, allowed
        except BaseException:
            conn.rollback()
            raise

    def release(self, rid: int | None) -> None:
        if rid:
            self.db.run("DELETE FROM budget_reservations WHERE id=?", (rid,))

    def settle(self, rid: int | None, agent: str, task_id, model: str, usage: dict) -> float:
        """Remplace la réservation par le coût réel (dans la même écriture)."""
        if rid:
            self.db.conn.execute("DELETE FROM budget_reservations WHERE id=?", (rid,))
        return self.record(agent, task_id, model, usage)

    def searches_left(self) -> int:
        return max(0, int(self.cfg["budget"]["web_searches_per_day"]) - self.db.searches_today())

    def record(self, agent: str, task_id, model: str, usage: dict) -> float:
        usd, inp, out, searches = compute_cost(self.cfg["prices"], model, usage)
        self.db.add_cost(agent, task_id, model, inp, out, searches, usd)
        return usd
