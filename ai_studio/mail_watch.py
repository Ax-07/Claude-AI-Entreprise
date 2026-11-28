"""Surveillance de la boîte mail en temps réel (IMAP IDLE) : le serveur prévient dès qu'un email arrive.

Tourne dans un fil séparé du moteur, pour que l'arrivée d'un email soit traitée même pendant qu'un agent travaille
ou que le moteur est en pause. En cas de coupure, nouvel essai après une minute ; le relevé périodique du moteur
reste actif en filet de sécurité.
"""
from __future__ import annotations

import imaplib
import threading
import time

IDLE_SECONDS = 25 * 60          # Gmail coupe une attente IDLE au bout d'environ 29 minutes


def wait_for_mail(M, timeout: float = IDLE_SECONDS) -> bool:
    """Attend (IDLE) qu'un message arrive. Renvoie True si la boîte a changé, False à l'expiration du délai."""
    tag = M._new_tag()
    tag = tag.decode() if isinstance(tag, bytes) else tag
    M.send(f"{tag} IDLE\r\n".encode())
    first = M.readline()
    if not first.startswith(b"+"):
        raise imaplib.IMAP4.error(f"IDLE refusé : {first!r}")
    sock = getattr(M, "sock", None)
    if sock is not None:
        sock.settimeout(timeout)
    changed = False
    try:
        line = M.readline()
        changed = b"EXISTS" in line or b"RECENT" in line
    except (TimeoutError, OSError):
        changed = False
    finally:
        if sock is not None:
            sock.settimeout(60)
        M.send(b"DONE\r\n")
        for _ in range(50):                               # on vide les réponses jusqu'à la fin de la commande IDLE
            line = M.readline()
            if not line or line.startswith(tag.encode()):
                break
            changed = changed or b"EXISTS" in line
    return changed


def watch(cfg, open_db, connect=None, stop: threading.Event | None = None, max_loops: int | None = None) -> None:
    """Boucle de surveillance. open_db() renvoie une connexion à la base propre à ce fil."""
    from .commerce import imap_settings, sync_inbox
    stop = stop or threading.Event()
    db = open_db()
    loops, last_error = 0, ""
    while not stop.is_set() and (max_loops is None or loops < max_loops):
        loops += 1
        try:
            host, user, pwd = imap_settings()
            M = connect() if connect else imaplib.IMAP4_SSL(host, 993, timeout=60)
            try:
                if not connect:
                    M.login(user, pwd)
                M.select("INBOX", readonly=True)
                if wait_for_mail(M):
                    sync_inbox(cfg, db)                  # même traitement que le relevé périodique (tâche, alerte…)
            finally:
                try:
                    M.logout()
                except Exception:
                    pass
            last_error = ""
        except Exception as e:                              # coupure réseau, mot de passe changé… : on réessaie
            msg = f"{type(e).__name__} : {e}"[:200]
            if msg != last_error:
                db.log("systeme", "email", f"Surveillance en temps réel de la boîte mail interrompue ({msg}) : nouvel essai dans 1 min")
                last_error = msg
            stop.wait(60)


def start(cfg, open_db) -> threading.Thread | None:
    from .commerce import imap_settings
    if not cfg.get("commerce", "realtime_inbox", default=True) or not imap_settings()[1]:
        return None
    t = threading.Thread(target=watch, args=(cfg, open_db), name="surveillance-emails", daemon=True)
    t.start()
    return t
