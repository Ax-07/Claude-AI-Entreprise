"""Modèles d'emails (catalogue/modeles_email.json, modifiable par le propriétaire sans redémarrer).

L'agent ne remplit que les champs personnalisés ; le nom de l'offre, son prix, son délai, les informations à demander
et le lien de paiement viennent du catalogue. Le rendu est refusé si un champ manque, dépasse sa longueur, ou
contient un texte à compléter.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
TODO_RE = re.compile(r"(?i)\[[^\]]{1,40}\]|\bX{3,}\b|à compléter|à remplir|lorem ipsum|\{|\}")
AUTO = {"offre", "prix", "delai", "infos_requises", "lien_paiement", "lien_avis", "suite", "besoin"}


def _path(cfg) -> Path | None:
    from .config import ROOT
    for base in (Path(cfg.root), ROOT):
        p = base / "catalogue" / "modeles_email.json"
        if p.exists():
            return p
    return None


def load(cfg) -> list[dict]:
    p = _path(cfg)
    if not p:
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"Modèles d'emails illisibles ({p.name}) : {e}")
    return [t for t in data if isinstance(t, dict) and t.get("id") and t.get("body")]


def get(cfg, tid: str) -> dict:
    for t in load(cfg):
        if t["id"] == tid:
            return t
    raise KeyError(f"Modèle « {tid} » introuvable (email_templates pour la liste)")


def _delay(hours: int) -> str:
    h = int(hours or 0)
    return f"{h // 24} jours" if h >= 48 and h % 24 == 0 else f"{h} heures"


def _price(eur: float) -> str:
    v = float(eur)
    return (f"{v:.0f}" if v.is_integer() else f"{v:.2f}".replace(".", ",")) + " €"


def offer_values(db, name: str) -> dict:
    o = db.one("SELECT * FROM offers WHERE lower(name)=lower(?) AND status='active'", (name.strip(),))
    if not o:
        raise ValueError(f"Offre « {name} » absente du catalogue actif (list_offers)")
    return {"offre": o["name"], "prix": _price(o["price_eur"]), "delai": _delay(o["delay_hours"]),
            "infos_requises": o["required_info"] or "les informations utiles à la réalisation",
            "lien_paiement": f"\n\nVous pouvez régler en ligne ici : {o['payment_link_url']}" if o["payment_link_url"] else ""}


def answer_form(infos: str) -> str:
    """Formulaire à copier-coller par le client dans sa réponse : une ligne par information à fournir."""
    items = [x.strip(" .") for x in re.split(r"\s*[;\n]\s*|\s*,\s*(?=[A-ZÉÈÀ0-9])", infos or "") if x.strip(" .")]
    if not items:
        return ""
    lines = [f"{it[0].upper()}{it[1:]} :" for it in items]
    return ("\n\nPour vous simplifier la tâche, copiez ce modèle dans votre réponse et complétez chaque ligne "
            "(laissez vide ce qui ne vous concerne pas) :\n\n----------\n" + "\n".join(lines) + "\n----------")


def order_values(cfg, db, n: int) -> dict:
    s = db.one("SELECT rowid AS n, * FROM sales WHERE rowid=?", (n,))
    if not s:
        raise ValueError(f"Commande n°{n} introuvable (list_orders)")
    o = db.one("SELECT * FROM offers WHERE id=?", (s["offer_id"],)) if s["offer_id"] else None
    name = o["name"] if o else (s["product"] or "commande")
    out = {"offre": name, "lien_avis": "", "suite": "",
           "infos_requises": (o["required_info"] if o and o["required_info"] else "les informations utiles à la réalisation"),
           "delai": _delay(o["delay_hours"]) if o else "le délai prévu"}
    url = (cfg.get("aftercare", "review_url", default="") or "").strip()
    if url:
        out["lien_avis"] = f"\n\nSi vous le souhaitez, vous pouvez aussi laisser un avis ici : {url}"
    nxt = db.one("SELECT * FROM offers WHERE lower(name)=lower(?) AND status='active'", (o["next_offer"],)) if o and o["next_offer"] else None
    bought = nxt and db.one("SELECT 1 FROM sales WHERE lower(customer_email)=lower(?) AND offer_id=?", (s["customer_email"] or "", nxt["id"]))
    if nxt and not bought and s["satisfied"] != 0:
        v = offer_values(db, nxt["name"])
        out["suite"] = (f"\n\nPour aller plus loin, nous réalisons aussi un {v['offre']} : {nxt['summary'][:200].rstrip('.')}. "
                        f"Livré sous {v['delai']}, pour {v['prix']}. Il suffit de répondre à cet email si cela vous intéresse.")
    return out


def render(cfg, db, tid: str, fields: dict | None) -> tuple[str, str, dict]:
    """→ (objet, texte, modèle). ValueError avec la liste précise de ce qui ne va pas."""
    t = get(cfg, tid)
    fields = {k: ("" if v is None else str(v)).strip() for k, v in (fields or {}).items()}
    spec = t.get("fields", {})
    errors = []
    unknown = [k for k in fields if k not in spec]
    if unknown:
        errors.append(f"champ(s) inconnu(s) : {', '.join(unknown)} (attendus : {', '.join(spec)})")
    values = {}
    for k, f in spec.items():
        v = " ".join(fields.get(k, "").split()) if k != "reponse" else fields.get(k, "")
        if not v and not f.get("optional"):
            errors.append(f"« {k} » manquant ({f.get('desc', '')})")
            continue
        if v and len(v) > int(f.get("max", 500)):
            errors.append(f"« {k} » trop long ({len(v)} caractères, {f.get('max')} au maximum)")
        if v and len(v) < int(f.get("min", 0)):
            errors.append(f"« {k} » trop court ({len(v)} caractères, {f.get('min')} au minimum)")
        if v and TODO_RE.search(v):
            errors.append(f"« {k} » contient un texte à compléter ou des crochets : écris la valeur réelle")
        values[k] = v
    if values.get("salutation"):
        values["salutation"] = " " + values["salutation"]
    offer = t.get("offer")
    if offer == "{commande}":                        # modèle lié à une commande : offre livrée et offre suivante
        try:
            values.update(order_values(cfg, db, int(values.get("commande") or 0)))
        except (ValueError, TypeError) as e:
            errors.append(str(e) if isinstance(e, ValueError) and "Commande" in str(e) else "« commande » doit être un numéro de commande")
        offer = None
    if values.get("remarque"):
        values["remarque"] = values["remarque"].rstrip() + "\n\n"
    if offer:
        name = values.get("offre", "") if offer == "{offre}" else offer
        try:
            values.update(offer_values(db, name) if name else {})
        except ValueError as e:
            errors.append(str(e))
    if "infos_manquantes" in spec and not errors:        # ne redemander que ce qui manque vraiment
        miss = values.get("infos_manquantes", "").strip()
        asking = t["id"] != "reponse_interesse"          # une demande d'informations demande tout si rien n'est précisé
        everything = miss.lower().rstrip(".") in ("toutes", "tout", "toutes les informations")
        full = values.get("infos_requises", "")
        if asking and (not miss or everything):
            values["besoin"] = f"Pour la réaliser, nous avons besoin des éléments suivants.{answer_form(full)}"
        elif asking:
            values["besoin"] = f"Pour la réaliser, il nous manque seulement quelques éléments.{answer_form(miss)}"
        elif not miss:
            values["besoin"] = "Nous avons bien toutes les informations nécessaires : la réalisation démarre dès votre paiement."
        elif everything:
            values["besoin"] = f"Pour réaliser le {values.get('offre', 'travail')}, j'aurais besoin des éléments suivants.{answer_form(full)}"
        else:
            values["besoin"] = f"Pour compléter, il nous manque seulement quelques éléments.{answer_form(miss)}"
    if errors:
        raise ValueError("Email non conforme au modèle « " + t["id"] + " » : " + " ; ".join(errors))
    fill = lambda s: PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), s)
    subject, body = " ".join(fill(t.get("subject", "")).split()), fill(t["body"])
    left = sorted(set(PLACEHOLDER_RE.findall(subject + body)))
    if left:
        raise ValueError(f"Modèle « {t['id']} » : variable(s) non remplie(s) {', '.join(left)}")
    return subject, body, t


def describe(cfg, db) -> str:
    out = []
    for t in load(cfg):
        line = [f"- {t['id']} — {t.get('name', '')}", f"  Quand : {t.get('usage', '')}"]
        if t.get("offer") and t["offer"] != "{offre}":
            try:
                v = offer_values(db, t["offer"])
                line.append(f"  Offre : {v['offre']} ({v['prix']}, {v['delai']}) — reprise automatiquement")
            except ValueError as e:
                line.append(f"  ⚠ {e}")
        line.append("  Champs à remplir (fields) : " + " ; ".join(
            f"{k}{' (facultatif)' if f.get('optional') else ''} = {f.get('desc', '')}" for k, f in t.get("fields", {}).items()))
        line.append(f"  Objet : {t.get('subject', '')}")
        out.append("\n".join(line))
    return ("Modèles d'emails (vouvoiement ; signature et phrase d'opposition ajoutées automatiquement) :\n"
            + "\n".join(out)) if out else "Aucun modèle d'email."
