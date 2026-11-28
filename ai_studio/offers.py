"""Validation des offres : un agent propose, la marge est calculée, le propriétaire décide.

Une offre créée par un agent est un brouillon (status 'draft') : elle ne peut être ni vendue (lien de paiement, page,
modèle d'email) ni livrée avant validation. La modification d'une offre active (prix, délai, contenu…) est mise en
attente dans `offers.pending` : l'offre continue d'être vendue telle quelle jusqu'à la décision.
Les offres importées par le propriétaire (catalogue/offres.json) sont actives directement.
"""
from __future__ import annotations

import json

from .db import iso

FIELDS = ("price_eur", "summary", "deliverable", "delay_hours", "required_info", "producer", "activity", "next_offer")
LABELS = {"price_eur": "Prix", "summary": "Résumé", "deliverable": "Livrable", "delay_hours": "Délai",
          "required_info": "Infos à demander au client", "producer": "Producteur", "activity": "Activité",
          "next_offer": "Offre suivante proposée après livraison"}


def margin(cfg, db, price_eur: float, offer_id: int | None = None) -> dict:
    """Marge par vente. Coût d'API : moyenne réelle des commandes livrées de cette offre si elles existent, sinon le
    plafond de dépense par mission (pire cas garanti par le moteur)."""
    c = cfg.get("offers", default={}) or {}
    rate = float(c.get("usd_to_eur", 0) or cfg.get("prices", "usd_to_eur", default=0.92))
    price = float(price_eur)
    stripe = round(price * float(c.get("stripe_pct", 0.028)) + float(c.get("stripe_fixed_eur", 0.25)), 2)
    social = round(price * float(c.get("social_rate", 0.256)), 2)
    api_usd, basis = None, ""
    if offer_id:
        rows = db.q("SELECT s.rowid AS n, (SELECT COALESCE(SUM(k.usd),0) FROM costs k JOIN tasks t ON t.id=k.task_id "
                    "WHERE t.order_ref=s.rowid) AS usd FROM sales s WHERE s.offer_id=? AND s.stage='livree'", (offer_id,))
        if rows:
            api_usd = sum(r["usd"] for r in rows) / len(rows)
            basis = f"moyenne réelle de {len(rows)} commande(s) livrée(s)"
    if api_usd is None:
        api_usd = float(cfg.get("missions", "max_usd_per_mission", default=3.0))
        basis = "plafond de dépense par mission (pire cas)"
    api = round(api_usd * rate, 2)
    net = round(price - stripe - social - api, 2)
    pct = round(100 * net / price, 1) if price else 0.0
    mini = float(c.get("min_margin_pct", 50))
    return {"price": price, "stripe": stripe, "social": social, "api": api, "api_basis": basis, "net": net, "pct": pct,
            "min_pct": mini, "ok": pct >= mini,
            "rates": (f"Stripe {float(c.get('stripe_pct', 0.028)) * 100:.1f} % + {float(c.get('stripe_fixed_eur', 0.25)):.2f} €, "
                      f"cotisations {float(c.get('social_rate', 0.256)) * 100:.1f} %").replace(".", ",")}


def margin_text(m: dict) -> str:
    e = lambda v: f"{v:.2f} €".replace(".", ",")
    return (f"Prix {e(m['price'])} − Stripe {e(m['stripe'])} − cotisations {e(m['social'])} − API {e(m['api'])} "
            f"({m['api_basis']}) = {e(m['net'])} net par vente, soit {m['pct']:.0f} % "
            + ("✓" if m["ok"] else f"⚠ sous le seuil de {m['min_pct']:.0f} %") + f" — {m['rates']}")


def submit(cfg, db, agent: str, task_id, values: dict, name: str) -> str:
    """Brouillon ou modification proposée → demande de validation avec le calcul de marge."""
    old = db.one("SELECT * FROM offers WHERE lower(name)=lower(?)", (name.strip(),))
    t = iso()
    if old and old["status"] == "active":
        changes = {k: v for k, v in values.items() if str(old[k] or "") != str(v or "")}
        if not changes:
            return f"Offre « {old['name']} » : aucune différence avec la version active, rien à valider."
        db.run("UPDATE offers SET pending=?, updated_at=? WHERE id=?", (json.dumps(changes, ensure_ascii=False), t, old["id"]))
        oid, kind = old["id"], "update"
    elif old:                                       # brouillon (ou offre archivée) : on le met à jour
        db.run(f"UPDATE offers SET {', '.join(f'{k}=?' for k in FIELDS)}, status='draft', pending=NULL, updated_at=? WHERE id=?",
               (*[values[k] for k in FIELDS], t, old["id"]))
        oid, kind, changes = old["id"], "new", values
    else:
        oid = db.run(f"INSERT INTO offers(name, {', '.join(FIELDS)}, status, created_by, created_at, updated_at) "
                     f"VALUES (?, {', '.join('?' * len(FIELDS))}, 'draft', ?, ?, ?)", (name.strip()[:120], *[values[k] for k in FIELDS], agent, t, t))
        kind, changes = "new", values
    m = margin(cfg, db, values["price_eur"], oid)
    for a in db.q("SELECT id FROM approvals WHERE action='offer' AND status='pending'"):   # une seule demande par offre
        if json.loads(db.approval(a["id"])["payload"]).get("offer_id") == oid:
            db.set_approval(a["id"], status="rejected", decided_at=t, result="Remplacée par une nouvelle proposition")
    aid = db.add_approval(agent, task_id, "offer", {"offer_id": oid, "name": old["name"] if old else name.strip()[:120],
                                                    "kind": kind, "changes": changes, "margin": m, "margin_text": margin_text(m)},
                          ("Nouvelle offre" if kind == "new" else "Modification d'une offre active") + f" — marge {m['pct']:.0f} %"
                          + ("" if m["ok"] else " ⚠ faible"))
    db.log(agent, "offre", f"Offre « {name} » soumise à validation (#{aid}) : marge {m['pct']:.0f} %")
    return (f"Offre « {name} » soumise à la validation du propriétaire (#{aid}) avec le calcul de marge : {margin_text(m)}. "
            + ("Elle reste en brouillon et ne peut pas être vendue avant sa décision." if kind == "new" else
               "La version actuelle reste en vente jusqu'à sa décision."))


def decide(db, payload: dict, approve: bool) -> str:
    o = db.one("SELECT * FROM offers WHERE id=?", (payload["offer_id"],))
    if not o:
        return "offre introuvable"
    t = iso()
    if approve:
        if payload["kind"] == "update":
            changes = json.loads(o["pending"] or "{}") or payload["changes"]
            db.run(f"UPDATE offers SET {', '.join(f'{k}=?' for k in changes)}, pending=NULL, updated_at=? WHERE id=?",
                   (*changes.values(), t, o["id"]))
        else:
            db.run("UPDATE offers SET status='active', pending=NULL, updated_at=? WHERE id=?", (t, o["id"]))
        db.log("proprietaire", "offre", f"Offre « {o['name']} » validée : elle peut être vendue")
        return f"offre « {o['name']} » validée et active"
    if payload["kind"] == "update":
        db.run("UPDATE offers SET pending=NULL, updated_at=? WHERE id=?", (t, o["id"]))
        return f"modification de « {o['name']} » refusée, la version actuelle reste en vente"
    db.run("UPDATE offers SET status='archived', updated_at=? WHERE id=?", (t, o["id"]))
    return f"offre « {o['name']} » refusée (archivée)"


def slug(name: str) -> str:
    import unicodedata, re
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def method_path(cfg, offer_name: str):
    """catalogue/methodes/<offre>.md (dossier du projet, ou celui du code), ou None."""
    from pathlib import Path
    from .config import ROOT
    for base in (Path(cfg.root), ROOT):
        p = base / "catalogue" / "methodes" / f"{slug(offer_name)}.md"
        if p.is_file():
            return p
    return None


def method_for_task(cfg, db, task) -> str:
    """Méthode de production à suivre pour une tâche d'une commande, confiée au producteur de l'offre."""
    if task["order_ref"] is None:
        return ""
    s = db.one("SELECT offer_id, product FROM sales WHERE rowid=?", (task["order_ref"],))
    o = db.one("SELECT name, producer FROM offers WHERE id=?", (s["offer_id"],)) if s and s["offer_id"] else None
    if not o or task["agent"] != o["producer"]:
        return ""
    p = method_path(cfg, o["name"])
    return p.read_text(encoding="utf-8")[:12000] if p else ""
