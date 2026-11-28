"""Bilan mensuel par offre, calculé sans appel d'API : ventes, chiffre d'affaires, coût d'API réel, marge, délais,
satisfaction, prospection. Le 1er du mois, le bilan du mois écoulé est écrit dans rapports/ et Finance l'analyse
(3 recommandations au plus, transmises au PDG)."""
from __future__ import annotations

from datetime import datetime

from .db import iso, now


def prev_month(ref: datetime | None = None) -> str:
    d = ref or now()
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return f"{y:04d}-{m:02d}"


def _hours(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600


def monthly(cfg, db, month: str | None = None) -> dict:
    month = month or now().strftime("%Y-%m")
    c = cfg.get("offers", default={}) or {}
    rate = float(cfg.get("prices", "usd_to_eur", default=0.92))
    pct, fixed, social = float(c.get("stripe_pct", 0.028)), float(c.get("stripe_fixed_eur", 0.25)), float(c.get("social_rate", 0.256))
    rows = []
    offers = db.q("SELECT * FROM offers WHERE status IN ('active','draft') OR id IN "
                  "(SELECT offer_id FROM sales WHERE substr(created_at,1,7)=?) ORDER BY id", (month,))
    for o in offers:
        sales = db.q("SELECT rowid AS n, * FROM sales WHERE offer_id=? AND substr(created_at,1,7)=?", (o["id"], month))
        real = [s for s in sales if s["livemode"]]
        revenue = sum(s["amount"] for s in real)
        api_usd = 0.0
        delivered, delays, late = 0, [], 0
        for s in sales:
            api_usd += db.one("SELECT COALESCE(SUM(k.usd),0) FROM costs k JOIN tasks t ON t.id=k.task_id WHERE t.order_ref=?", (s["n"],))[0]
            if s["stage"] == "livree" and s["delivered_at"]:
                delivered += 1
                delays.append(_hours(s["info_received_at"] or s["created_at"], s["delivered_at"]))   # le délai court dès les infos reçues
                late += bool(s["due_at"] and s["delivered_at"] > s["due_at"])
        fees = sum(s["amount"] * pct + fixed for s in real)
        api_eur = api_usd * rate
        net = revenue - fees - revenue * social - api_eur * (len(real) / len(sales) if sales else 0)
        happy = sum(1 for s in sales if s["satisfied"] == 1)
        unhappy = sum(1 for s in sales if s["satisfied"] == 0)
        pr = db.q("SELECT status FROM prospects WHERE lower(offer)=lower(?) AND substr(COALESCE(first_contact_at,''),1,7)=?", (o["name"], month))
        contacted = len(pr)
        answered = sum(1 for p in pr if p["status"] in ("repondu", "interesse", "client", "refus", "stop"))
        rows.append({"offer": o["name"], "status": o["status"], "price": o["price_eur"], "promised_h": o["delay_hours"],
                     "sales": len(real), "test_orders": len(sales) - len(real), "revenue": round(revenue, 2),
                     "api_eur": round(api_eur, 2), "api_per_order_eur": round(api_eur / len(sales), 2) if sales else None,
                     "net": round(net, 2), "margin_pct": round(100 * net / revenue, 1) if revenue else None,
                     "delivered": delivered, "avg_delivery_h": round(sum(delays) / len(delays), 1) if delays else None, "late": late,
                     "satisfied": happy, "unsatisfied": unhappy,
                     "prospects": contacted, "answered": answered,
                     "response_pct": round(100 * answered / contacted, 1) if contacted else None,
                     "clients": sum(1 for p in pr if p["status"] == "client")})
    month_usd = db.one("SELECT COALESCE(SUM(usd),0) FROM costs WHERE month=?", (month,))[0]
    tot_rev = sum(r["revenue"] for r in rows)
    return {"month": month, "offers": rows, "revenue": round(tot_rev, 2), "api_month_eur": round(month_usd * rate, 2),
            "net_after_all_api": round(sum(r["net"] for r in rows) - (month_usd * rate - sum(r["api_eur"] for r in rows)), 2),
            "generated_at": iso()}


def to_markdown(r: dict) -> str:
    e = lambda v: "—" if v is None else f"{v:,.2f} €".replace(",", " ").replace(".", ",")
    p = lambda v: "—" if v is None else f"{v:.0f} %"
    lines = [f"# Bilan du mois {r['month']}", "",
             f"- Chiffre d'affaires réel : {e(r['revenue'])}",
             f"- Dépenses d'API du mois (toutes activités) : {e(r['api_month_eur'])}",
             f"- Résultat après frais Stripe, cotisations et toutes les dépenses d'API : {e(r['net_after_all_api'])}", "",
             "| Offre | Ventes | CA | API / commande | Marge | Livrées | Délai moyen (promis) | Retards | Satisfaction | Prospects contactés | Taux de réponse | Clients |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for o in r["offers"]:
        lines.append(f"| {o['offer']}{' (brouillon)' if o['status'] == 'draft' else ''} | {o['sales']}"
                     + (f" (+{o['test_orders']} essai)" if o["test_orders"] else "")
                     + f" | {e(o['revenue'])} | {e(o['api_per_order_eur'])} | {e(o['net'])} ({p(o['margin_pct'])}) | {o['delivered']}"
                     + f" | {'—' if o['avg_delivery_h'] is None else str(o['avg_delivery_h']) + ' h'} ({o['promised_h']} h) | {o['late']}"
                     + f" | {o['satisfied']} 👍 / {o['unsatisfied']} 👎 | {o['prospects']} | {p(o['response_pct'])} | {o['clients']} |")
    if not r["offers"]:
        lines.append("| (aucune offre) |  |  |  |  |  |  |  |  |  |  |  |")
    lines += ["", "Marge = CA − frais Stripe − cotisations − coût d'API des commandes (taux de config.json, section offers). "
              "Les commandes d'essai ne comptent pas dans le chiffre d'affaires."]
    return "\n".join(lines) + "\n"


def run_monthly(cfg, db) -> str | None:
    """Le 1er du mois (ou au premier cycle après) : bilan du mois écoulé + tâche d'analyse pour Finance."""
    if not cfg.get("reports", "monthly", default=True):
        return None
    month = prev_month()
    if db.kv_get("monthly_report_done") == month:
        return None
    db.kv_set("monthly_report_done", month)
    r = monthly(cfg, db, month)
    if not r["offers"] and not r["api_month_eur"]:
        return None                                  # rien à analyser (entreprise pas encore active)
    path = f"rapports/bilan-{month}.md"
    f = cfg.workspace / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(to_markdown(r), encoding="utf-8")
    tid = db.add_task(f"Bilan mensuel {month}", "finance",
                      f"Le bilan chiffré du mois {month} est prêt : {path} (calculé automatiquement, chiffres exacts). "
                      "Lis-le (read_file), puis ajoute à la fin du fichier (write_file avec append=true) une section "
                      "« Analyse » : ce qui marche, ce qui coûte trop ou se vend mal, et AU PLUS 3 recommandations concrètes "
                      "(prix, offre à arrêter ou à pousser, coût d'API à réduire, prospection) avec le chiffre qui la justifie. "
                      "Puis transmets les recommandations au PDG (delegate_task) : c'est lui qui décide, et une modification "
                      "d'offre passe par la validation du propriétaire. N'invente aucun chiffre.",
                      priority=2, created_by="systeme")
    db.log("finance", "rapport", f"Bilan du mois {month} écrit dans {path} (analyse : tâche #{tid})", tid)
    from .commerce import _notify
    _notify(cfg, db, f"Bilan du mois {month}", f"CA {r['revenue']:.2f} € · API {r['api_month_eur']:.2f} € · "
            f"résultat {r['net_after_all_api']:.2f} € — détail dans {path}", "reports")
    return path
