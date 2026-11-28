"""Garanties sur les emails qui sortent (audit SEC-05 et SEC-06).

1. Mode test : la liste des destinataires autorisés est vérifiée au DERNIER moment, dans l'exécuteur SMTP,
   quel que soit le chemin suivi (send_email, deliver_order, destinataire modifié par le propriétaire…).
2. Pièces jointes figées : au moment où la demande est créée, le fichier est copié hors de l'espace de travail
   des agents (data/pieces_validees/<sha256>/), avec son empreinte SHA-256. L'aperçu du propriétaire et l'envoi
   utilisent cette copie, et l'empreinte est revérifiée juste avant l'envoi : ce qui part est exactement ce qui a
   été validé, même si un agent modifie le fichier entre-temps.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
from pathlib import Path


class OutgoingError(RuntimeError):
    """L'email ne doit pas partir (destinataire interdit, pièce jointe absente ou modifiée)."""


# ---------------------------------------------------------------- mode test
def check_recipient(cfg, to: str) -> None:
    """Lève OutgoingError si le mode test est actif et que le destinataire n'est pas dans la liste autorisée."""
    from .config import test_recipients
    allowed = test_recipients(cfg)
    if allowed is not None and str(to or "").strip().lower() not in allowed:
        raise OutgoingError(f"mode test actif : {to} n'est pas un destinataire autorisé "
                            f"({', '.join(allowed) or 'aucun'}). Rien n'a été envoyé.")


# ---------------------------------------------------------------- pièces jointes figées
def frozen_dir(cfg) -> Path:
    return Path(cfg.data_dir) / "pieces_validees"          # hors de data/workspace : inaccessible aux agents


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def freeze(cfg, rel: str) -> dict:
    """Copie la pièce jointe de l'espace de travail dans le coffre et renvoie les champs à mettre dans la demande."""
    base = cfg.workspace.resolve()
    src = (base / rel).resolve()
    if base not in src.parents or not src.is_file():
        raise OutgoingError(f"pièce jointe introuvable : {rel}")
    digest = sha256_file(src)
    dest = frozen_dir(cfg) / digest / src.name
    if not (dest.is_file() and sha256_file(dest) == digest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copyfile(src, tmp)
        if sha256_file(tmp) != digest:                       # le fichier a changé pendant la copie
            tmp.unlink(missing_ok=True)
            raise OutgoingError(f"la pièce jointe {rel} a été modifiée pendant sa préparation : réessaie.")
        if dest.exists():
            os.chmod(dest, stat.S_IWRITE | stat.S_IREAD)
        os.replace(tmp, dest)
        os.chmod(dest, stat.S_IREAD)                         # lecture seule (aussi sous Windows)
    return {"attachment": rel, "attachment_sha256": digest, "attachment_size": dest.stat().st_size}


def frozen_path(cfg, payload: dict) -> Path:
    """Copie figée de la pièce jointe, après vérification de son empreinte. Lève OutgoingError sinon."""
    rel, digest = payload.get("attachment"), payload.get("attachment_sha256")
    if not rel:
        raise OutgoingError("aucune pièce jointe")
    if not digest:
        raise OutgoingError("pièce jointe non figée (demande créée avant la vérification d'empreinte) : refuse cette "
                            "demande et fais refaire la livraison pour qu'elle soit figée.")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise OutgoingError("empreinte de pièce jointe invalide")
    p = frozen_dir(cfg) / digest / Path(rel).name
    if not p.is_file():
        raise OutgoingError(f"copie validée de {rel} introuvable : rien n'a été envoyé.")
    if sha256_file(p) != digest:
        raise OutgoingError(f"la copie validée de {rel} a été modifiée depuis la validation : rien n'a été envoyé.")
    return p
