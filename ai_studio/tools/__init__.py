"""Registre des outils mis à disposition des agents.

Chaque outil a : un nom, une description (lue par le modèle), un schéma d'entrée JSON
et une fonction Python. Les agents n'ont accès qu'aux outils listés dans leur rôle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ToolContext:
    cfg: object
    db: object
    agent: str
    task: object                       # sqlite Row de la tâche en cours
    agent_names: list[str]
    finished: bool = False
    result: str = ""
    notes: list[str] = field(default_factory=list)
    # Outils réellement proposés au modèle pour cette exécution. None = ceux du rôle de l'agent (ROLES).
    # C'est le code qui fait foi, pas le modèle : tout outil hors de cette liste est refusé (SEC-01).
    allowed: frozenset | None = None


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    func: Callable
    sensitive: bool = False            # True = passe par la validation du propriétaire
    requires_env: tuple = ()           # variables .env sans lesquelles l'outil n'est pas proposé aux agents

    def available(self) -> bool:
        import os
        return all(os.environ.get(k) for k in self.requires_env)

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict, required: list[str] | None = None, sensitive: bool = False,
         requires_env: tuple = ()):
    def deco(fn):
        REGISTRY[name] = Tool(name, description,
                              {"type": "object", "properties": properties, "required": required or []},
                              fn, sensitive, tuple(requires_env))
        return fn
    return deco


ALIASES = {"draft_email": "send_email"}   # ancien nom, gardé pour les tâches en cours


def allowed_tools(ctx: ToolContext) -> frozenset:
    """Outils que l'agent du contexte a le droit d'exécuter. Refus par défaut : agent inconnu = aucun outil."""
    if ctx.allowed is not None:
        return frozenset(ctx.allowed)
    from ..agents import ROLES
    role = ROLES.get(ctx.agent)
    return frozenset(role.granted_tools()) if role else frozenset()


def run_tool(name: str, ctx: ToolContext, args: dict) -> str:
    canonical = ALIASES.get(name, name)
    t = REGISTRY.get(canonical)
    if not t:
        return f"Erreur : outil inconnu « {name} »."
    if canonical not in allowed_tools(ctx):
        _log_refusal(ctx, canonical, "outil non autorisé pour ce rôle")
        return (f"Refusé : l'outil « {canonical} » n'est pas autorisé pour l'agent « {ctx.agent} ». "
                "Utilise uniquement les outils qui te sont proposés, ou délègue au pôle compétent.")
    if not t.available():
        _log_refusal(ctx, canonical, "configuration manquante")
        return f"Refusé : l'outil « {canonical} » n'est pas configuré (variable .env manquante)."
    if isinstance(args, dict) and "_arguments_invalides" in args:
        return (f"Erreur : les arguments de {name} sont invalides ou ont été coupés (réponse trop longue). "
                "Réessaie avec un contenu plus court ; pour un long document, écris-le en plusieurs appels "
                "write_file (le premier normal, les suivants avec append=true), environ 1 000 mots par appel.")
    try:
        out = t.func(ctx, **(args or {}))
    except TypeError as e:
        return f"Erreur d'arguments pour {name} : {e}"
    except Exception as e:  # un outil ne doit jamais faire tomber l'agent
        return f"Erreur pendant {name} : {type(e).__name__} : {e}"
    out = "" if out is None else str(out)
    return out[:12000]


def _log_refusal(ctx: ToolContext, name: str, why: str) -> None:
    """Trace le refus dans le journal : un appel hors périmètre est un signal pour la Cybersécurité et l'audit RH."""
    try:
        tid = ctx.task["id"] if ctx.task is not None else None
        ctx.db.log(ctx.agent, "refus", f"Appel refusé à {name} ({why})", tid)
    except Exception:
        pass


# Enregistre les outils
from . import core, business, commerce  # noqa: E402,F401
