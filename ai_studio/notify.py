"""Notifications Windows (toasts) pour prévenir le propriétaire sans qu'il ait à surveiller le tableau de bord.

Aucune dépendance : le toast est affiché par PowerShell via l'API WinRT intégrée à Windows 10/11.
Le texte n'est jamais inséré dans la commande PowerShell : il passe par une variable d'environnement
(XML déjà échappé), ce qui empêche toute injection de commande par un texte produit par un agent.
Cliquer sur la notification ouvre le tableau de bord.

Hors Windows (tests, Linux), les notifications sont simplement ignorées.
"""
from __future__ import annotations

import os
import subprocess
import sys
from xml.sax.saxutils import escape

# Identifiant d'application de PowerShell : permet d'afficher un toast sans installer de raccourci.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

PS_SCRIPT = (
    "$ErrorActionPreference = 'Stop';"   # toute erreur → code de sortie 1 et message lisible (au lieu d'un succès trompeur)
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
    "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml($env:AI_STUDIO_TOAST);"
    "$t = New-Object Windows.UI.Notifications.ToastNotification $x;"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:AI_STUDIO_APPID).Show($t)"
)

ACTIONS = {"send_email": "Envoi d'email", "stripe_payment_link": "Lien de paiement Stripe", "decision": "Décision demandée",
           "publish_page": "Publication d'une page"}


def toast_xml(title: str, message: str, url: str) -> str:
    title, message = title[:120], message[:400]
    return (f'<toast activationType="protocol" launch="{escape(url, {chr(34): "&quot;"})}">'
            f'<visual><binding template="ToastGeneric"><text>{escape(title)}</text><text>{escape(message)}</text></binding></visual>'
            f'<actions><action content="Ouvrir le tableau de bord" activationType="protocol" arguments="{escape(url, {chr(34): "&quot;"})}"/></actions>'
            f'</toast>')


def _windows_runner(xml: str) -> bool:
    env = dict(os.environ, AI_STUDIO_TOAST=xml, AI_STUDIO_APPID=APP_ID)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", PS_SCRIPT],
                       env=env, capture_output=True, timeout=30, creationflags=flags)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"PowerShell a échoué (code {r.returncode}) : {err[:500]}")
    return True


# Remplaçable dans les tests.
runner = _windows_runner if sys.platform == "win32" else None


def send(cfg, db, title: str, message: str) -> bool:
    """Affiche une notification. Ne lève jamais d'exception : une notification ratée ne doit pas arrêter le moteur."""
    if not cfg.get("notifications", "windows", default=True) or runner is None:
        return False
    url = db.kv_get("dashboard_url") or "http://127.0.0.1:8765"
    try:
        ok = runner(toast_xml(title, message, url))
    except Exception as e:  # PowerShell absent, délai dépassé…
        ok = False
        db.log("systeme", "notification", f"Notification impossible : {e}")
    return ok


def check(cfg, db, stats: dict) -> list[str]:
    """Appelé à la fin de chaque cycle. Renvoie les titres des notifications envoyées (pour les tests)."""
    sent: list[str] = []
    n = cfg["notifications"]

    if n.get("approvals", True):
        last = int(db.kv_get("notified_approval_id", "0") or 0)
        new = [a for a in db.pending_approvals() if a["id"] > last]
        if new:
            db.kv_set("notified_approval_id", str(max(a["id"] for a in new)))
            total = len(db.pending_approvals())
            if len(new) == 1:
                a = new[0]
                title = f"Validation demandée : {ACTIONS.get(a['action'], a['action'])}"
                msg = f"Agent {a['agent']} — {a['reason'] or 'à valider dans le tableau de bord'}"
            else:
                title = f"{len(new)} nouvelles validations demandées"
                msg = ", ".join(f"{ACTIONS.get(a['action'], a['action'])} ({a['agent']})" for a in new[:4])
            if total > len(new):
                msg += f" — {total} en attente au total"
            if send(cfg, db, title, msg):
                sent.append(title)

    for key, kind, title in (("budget", "paused", "Moteur en pause : budget atteint"),
                             ("errors", "error", "Moteur bloqué : erreur de configuration")):
        value = stats.get(kind)
        if not n.get(key, True):
            continue
        if not value:
            db.kv_set(f"notified_{kind}", "")   # le problème est résolu : on pourra prévenir à nouveau
            continue
        if db.kv_get(f"notified_{kind}") == value[:300]:
            continue                              # déjà signalé, on ne harcèle pas
        db.kv_set(f"notified_{kind}", value[:300])
        if send(cfg, db, title, value):
            sent.append(title)
    return sent
