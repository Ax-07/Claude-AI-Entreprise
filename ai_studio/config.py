"""Chargement de la configuration (config.json) et des secrets (.env).

Les secrets (clé API, clés Stripe, SMTP) ne sont JAMAIS écrits dans config.json :
ils restent dans le fichier .env, qui n'est pas versionné.
"""
from __future__ import annotations

import copy
import json
import re
import os
from pathlib import Path

ROOT = Path(os.environ.get("AI_STUDIO_HOME", Path(__file__).resolve().parent.parent))

DEFAULTS: dict = {
    "company": {
        "name": "AI Enterprise Studio",
        "owner_name": "Propriétaire",
        "language": "fr",
        "description": "Micro-entreprise française de services numériques opérée par des agents IA.",
        # Mentions légales affichées en pied de toutes les pages publiques (obligatoires pour une page de vente).
        "legal": {"name": "", "siret": "", "address": "", "email": "", "vat_exempt": True, "cgv_url": ""},
        # Mode test (le temps du développement) : identité légale fictive pour les emails, envoi limité à ces
        # adresses (celles du propriétaire, qui accepte d'être contacté). CLI : mode-test on EMAIL / mode-test off.
        "test_mode": {"enabled": False, "recipients": []},
    },
    # "auto" : Anthropic si ANTHROPIC_API_KEY est dans .env, sinon OpenAI si OPENAI_API_KEY y est.
    "provider": "auto",
    "models": {
        # Par fournisseur : modèle économique pour le travail courant, modèle plus fort pour l'agent PDG.
        "anthropic": {"default": "claude-haiku-5-5", "strong": "claude-sonnet-5-5"},
        "openai": {"default": "gpt-6-luna", "strong": "gpt-6.1-terra"},
    },
    "openai": {
        # "none" est requis pour utiliser les outils avec les modèles à raisonnement via Chat Completions.
        "reasoning_effort": "none",
    },
    # Prix en USD par million de tokens (à mettre à jour si les fournisseurs changent leurs tarifs).
    "prices": {
        "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "long_input": 0.50, "long_output": 2.50, "long_threshold": 100000},
        "claude-sonnet-5-5": {"input": 2.0, "output": 10.0},
        "claude-opus-5-5": {"input": 4.0, "output": 20.0},
        "gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "output": 0.50},
        "gpt-6.1-terra": {"input": 1.0, "cached_input": 0.05, "output": 5.0},
        "gpt-6.2-sol": {"input": 2.0, "cached_input": 0.10, "output": 10.0},
        "gpt-5-nano": {"input": 0.05, "cached_input": 0.005, "output": 0.40},
        "gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.0},
        "web_search_per_call": 0.01,          # recherche web serveur Anthropic
        "usd_to_eur": 0.92,                   # conversion des coûts d'API (USD) pour calculer les marges en euros
        "brave_search_per_call": 0.005,       # Brave Search API (5 $ / 1 000 requêtes)
    },
    "budget": {
        "monthly_usd": 20.0,
        "daily_usd": 0.65,
        "per_task_usd": 0.15,
        "web_searches_per_day": 30,
    },
    "runtime": {
        "cycle_seconds": 60,
        "keep_full_tool_results": 2,          # derniers échanges d'outils gardés en entier ; les plus anciens sont résumés
        "old_tool_result_chars": 600,
        "max_tasks_per_cycle": 3,
        "max_steps_per_task": 8,
        "max_output_tokens": 6000,
        "max_open_tasks": 30,
        "max_delegation_depth": 3,
        "max_attempts": 2,
        "ceo_review_minutes": 180,
        "request_timeout_seconds": 120,
    },
    "server_tools": {
        "web_search": {"enabled": True, "type": "web_search_20260209", "max_uses": 3},
        "web_fetch": {"enabled": False, "type": "web_fetch_20260209", "max_uses": 3, "max_content_tokens": 6000},
    },
    # Notifications Windows : nouvelle validation à faire, budget atteint, erreur bloquante.
    "notifications": {"windows": True, "approvals": True, "budget": True, "errors": True, "sales": True, "emails": True},
    # Boucle commerciale : fréquence de lecture de Stripe et de la boîte mail, plafond de réponses par jour.
    # Relecture juridique avant ta décision (l'avis s'affiche sur la carte de validation, sans la bloquer).
    # Emails de prospection : désactivé par défaut (coût), à activer quand la prospection démarre.
    "security": {"daily_review": True},
    "finance": {"daily_review": True},                  # contrôle quotidien des dépenses par la Trésorerie
    "missions": {"max_usd_per_mission": 3.0},           # plafond de dépense d'une mission pilotée par un chef de projet
    "legal_review": {"enabled": True, "actions": ["publish_page", "stripe_payment_link"], "prospection_emails": False},
    # Suivi des commandes : relance du PDG si plus personne ne travaille sur une commande non livrée.
    "commerce": {"stripe_sync_minutes": 10, "inbox_sync_minutes": 5, "inbox_tasks_per_day": 10,
                 "order_nudge_minutes": 30, "order_max_nudges": 3, "client_wait_hours": 48,
                 "realtime_inbox": True},          # surveillance en continu de la boîte mail (IMAP IDLE), en plus du relevé périodique
    # Prospection : une seule relance, N jours après le premier email sans réponse ; sans suite N jours après la relance.
    "prospects": {"followup_days": 7, "close_after_days": 7},
    # Sauvegarde quotidienne de la base et des livrables dans data/sauvegardes (les N plus récentes sont gardées).
    "backup": {"enabled": True, "keep": 14},
    "email": {
        "from_name": "AI Enterprise Studio",
        "daily_limit": 20,
    },
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_env(path: Path | None = None) -> None:
    """Charge un fichier .env simple (CLE=valeur) sans écraser l'environnement existant."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


class Config:
    def __init__(self, data: dict, root: Path):
        self.data = data
        self.root = root
        self.data_dir = root / "data"
        self.workspace = self.data_dir / "workspace"
        self.db_path = self.data_dir / "studio.db"

    def __getitem__(self, key):
        return self.data[key]

    def get(self, *keys, default=None):
        cur = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def ensure_dirs(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)


EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[a-zA-Z]{2,}$")
TEST_LEGAL = {"name": "AI Enterprise Studio (TEST)", "siret": "000 000 000 00000 (fictif)", "address": "Adresse de test"}


def test_recipients(cfg) -> list[str] | None:
    """Adresses autorisées si le mode test est actif, sinon None."""
    t = cfg.get("company", "test_mode", default={}) or {}
    return [r.strip().lower() for r in t.get("recipients", []) if r.strip()] if t.get("enabled") else None


def sender_legal(cfg) -> dict:
    """Identité de l'expéditeur des emails : la vraie, complétée par des valeurs fictives en mode test."""
    legal = dict(cfg["company"].get("legal") or {})
    rec = test_recipients(cfg)
    if rec is not None:
        for k, v in {**TEST_LEGAL, "email": rec[0] if rec else ""}.items():
            legal[k] = legal.get(k) or v
    return legal


def load_config(root: Path | None = None) -> Config:
    root = Path(root) if root else ROOT
    load_env(root / ".env")
    user = {}
    cfg_file = root / "config.json"
    if cfg_file.exists():
        user = json.loads(cfg_file.read_text(encoding="utf-8"))
    cfg = Config(_deep_merge(DEFAULTS, user), root)
    cfg.ensure_dirs()
    return cfg
