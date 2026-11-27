"""Client de l'API Claude (sans dépendance externe) + calcul des coûts + plafonds budgétaires."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class LLMError(Exception):
    pass


class BudgetExceeded(Exception):
    """Levée quand un plafond (jour, mois, tâche, recherches) est atteint : le travail s'arrête proprement."""


class AnthropicClient:
    def __init__(self, api_key: str | None = None, timeout: int = 120):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.timeout = timeout
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY manquante : ajoute-la dans le fichier .env")

    def complete(self, model: str, system: list | str, messages: list, tools: list | None = None,
                 max_tokens: int = 2000) -> dict:
        body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            body["tools"] = tools
        data = json.dumps(body).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
        }
        delay = 5
        for attempt in range(4):
            req = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:800]
                if e.code in (429, 500, 502, 503, 529) and attempt < 3:
                    time.sleep(delay); delay *= 2
                    continue
                raise LLMError(f"API {e.code} : {detail}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 3:
                    time.sleep(delay); delay *= 2
                    continue
                raise LLMError(f"Réseau : {e}") from None
        raise LLMError("Échec après plusieurs tentatives")


def compute_cost(prices: dict, model: str, usage: dict) -> tuple[float, int, int, int]:
    """Retourne (usd, tokens_entrée, tokens_sortie, recherches_web) pour une réponse de l'API."""
    p = prices.get(model) or prices.get("claude-sonnet-5-5") or {"input": 3.0, "output": 15.0}
    inp = int(usage.get("input_tokens", 0))
    cache_w = int(usage.get("cache_creation_input_tokens", 0) or 0)
    cache_r = int(usage.get("cache_read_input_tokens", 0) or 0)
    out = int(usage.get("output_tokens", 0))
    total_in = inp + cache_w + cache_r
    pin, pout = p["input"], p["output"]
    if "long_threshold" in p and total_in > p["long_threshold"]:
        pin, pout = p.get("long_input", pin), p.get("long_output", pout)
    usd = (inp * pin + cache_w * pin * 1.25 + cache_r * pin * 0.1 + out * pout) / 1_000_000
    searches = int((usage.get("server_tool_use") or {}).get("web_search_requests", 0) or 0)
    usd += searches * float(prices.get("web_search_per_call", 0.01))
    return round(usd, 6), total_in, out, searches


class BudgetGuard:
    """Vérifie les plafonds AVANT chaque appel et enregistre la dépense APRÈS."""

    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db

    def check(self, task_id: int | None = None) -> None:
        b = self.cfg["budget"]
        if self.db.spent("month") >= b["monthly_usd"]:
            raise BudgetExceeded(f"Budget mensuel atteint ({b['monthly_usd']} $)")
        if self.db.spent("day") >= b["daily_usd"]:
            raise BudgetExceeded(f"Budget du jour atteint ({b['daily_usd']} $)")
        if task_id is not None and self.db.task_cost(task_id) >= b["per_task_usd"]:
            raise BudgetExceeded(f"Plafond par tâche atteint ({b['per_task_usd']} $)")

    def searches_left(self) -> int:
        return max(0, int(self.cfg["budget"]["web_searches_per_day"]) - self.db.searches_today())

    def record(self, agent: str, task_id, model: str, usage: dict) -> float:
        usd, inp, out, searches = compute_cost(self.cfg["prices"], model, usage)
        self.db.add_cost(agent, task_id, model, inp, out, searches, usd)
        return usd
