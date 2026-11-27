"""Sauvegarde quotidienne de la base (mémoire, tâches, coûts, validations) et des livrables des agents.

La copie de la base utilise l'API de sauvegarde de SQLite : elle est cohérente même pendant que le moteur écrit.
Les sauvegardes vont dans data/sauvegardes/AAAA-MM-JJ/ ; seules les N plus récentes sont conservées.
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def backup_dir(cfg) -> Path:
    return Path(cfg.db_path).parent / "sauvegardes"


def make_backup(cfg, db, label: str | None = None) -> Path:
    label = label or datetime.now().strftime("%Y-%m-%d")
    dest = backup_dir(cfg) / label
    dest.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(str(dest / "studio.db"))
    try:
        db.conn.backup(target)
    finally:
        target.close()
    ws = Path(cfg.workspace)
    if ws.exists():
        shutil.make_archive(str(dest / "livrables"), "zip", ws)
    prune(cfg)
    return dest


def prune(cfg) -> list[str]:
    keep = max(1, int(cfg.get("backup", "keep", default=14)))
    root = backup_dir(cfg)
    if not root.exists():
        return []
    dirs = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.name)
    removed = []
    for d in dirs[:-keep]:
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d.name)
    return removed


def daily(cfg, db) -> Path | None:
    """Une sauvegarde par jour au maximum. Ne lève jamais d'exception (le moteur doit continuer)."""
    if not cfg.get("backup", "enabled", default=True):
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    if db.kv_get("last_backup_day") == today:
        return None
    try:
        dest = make_backup(cfg, db, today)
    except Exception as e:
        db.log("systeme", "sauvegarde", f"Sauvegarde impossible : {e}")
        return None
    db.kv_set("last_backup_day", today)
    db.log("systeme", "sauvegarde", f"Sauvegarde du jour : {dest}")
    return dest


def list_backups(cfg) -> list[dict]:
    root = backup_dir(cfg)
    if not root.exists():
        return []
    out = []
    for d in sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True):
        size = sum(f.stat().st_size for f in d.iterdir() if f.is_file())
        out.append({"name": d.name, "path": str(d), "size": size})
    return out


def restore(cfg, name: str) -> Path:
    """Remplace la base actuelle par une sauvegarde (le moteur doit être arrêté). L'ancienne base est conservée à côté."""
    src = backup_dir(cfg) / name / "studio.db"
    if not src.exists():
        raise FileNotFoundError(f"Sauvegarde introuvable : {name}")
    db_path = Path(cfg.db_path)
    if db_path.exists():
        shutil.copy2(db_path, db_path.with_name(f"studio.avant-restauration-{datetime.now():%Y%m%d-%H%M%S}.db"))
    for ext in ("-wal", "-shm"):
        Path(str(db_path) + ext).unlink(missing_ok=True)
    shutil.copy2(src, db_path)
    return db_path
