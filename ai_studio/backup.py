"""Sauvegardes complètes et vérifiées (audit OPS-01).

Chaque sauvegarde (data/sauvegardes/AAAA-MM-JJ/) contient :
- studio.db : copie cohérente de la base (API de sauvegarde SQLite, même pendant que le moteur écrit) ;
- livrables.zip : l'espace de travail des agents (livrables, rapports, analyses…) ;
- pieces_validees.zip : le coffre des pièces jointes figées au moment des validations ;
- config.json : la configuration (sans secret : les clés restent dans .env, jamais sauvegardé) ;
- manifeste.json : empreinte SHA-256 et taille de chaque fichier.

La sauvegarde est vérifiée juste après sa création (intégrité SQLite, archives lisibles, empreintes). La restauration
vérifie le manifeste AVANT de toucher à quoi que ce soit, puis remplace base, livrables et coffre ensemble ; l'état
actuel est mis de côté dans data/archives/avant-restauration-<date>/ (rien n'est effacé).

Copie externe (backup.external_dir dans config.json : OneDrive, clé USB, disque réseau…) : chaque sauvegarde y est
copiée, pour survivre à une panne du PC. Chiffrement : la bibliothèque standard de Python ne sait pas écrire d'archive
chiffrée ; choisis donc un emplacement déjà chiffré (BitLocker, coffre-fort OneDrive).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

FILES = ("studio.db", "livrables.zip", "pieces_validees.zip", "config.json")


class BackupError(RuntimeError):
    pass


def backup_dir(cfg) -> Path:
    return Path(cfg.db_path).parent / "sauvegardes"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _vault(cfg) -> Path:
    return Path(cfg.data_dir) / "pieces_validees"


def make_backup(cfg, db, label: str | None = None) -> Path:
    label = label or datetime.now().strftime("%Y-%m-%d")
    dest = backup_dir(cfg) / label
    tmp = dest.with_name(dest.name + ".en-cours")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    target = sqlite3.connect(str(tmp / "studio.db"))
    try:
        db.conn.backup(target)
    finally:
        target.close()
    for name, src in (("livrables", Path(cfg.workspace)), ("pieces_validees", _vault(cfg))):
        if src.exists():
            shutil.make_archive(str(tmp / name), "zip", src)
        else:
            with zipfile.ZipFile(tmp / f"{name}.zip", "w"):
                pass
    conf = Path(cfg.root) / "config.json" if hasattr(cfg, "root") else None
    if conf and conf.is_file():
        shutil.copy2(conf, tmp / "config.json")
    manifest = {"created_at": datetime.now().isoformat(timespec="seconds"), "files": {
        f.name: {"sha256": _sha256(f), "size": f.stat().st_size} for f in sorted(tmp.iterdir()) if f.is_file()}}
    (tmp / "manifeste.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    problems = verify_dir(tmp)
    if problems:
        shutil.rmtree(tmp, ignore_errors=True)
        raise BackupError("sauvegarde invalide : " + " ; ".join(problems))
    if dest.exists():
        shutil.rmtree(dest, onerror=_force)
    os.replace(tmp, dest)
    prune(cfg)
    return dest


def verify_dir(d: Path) -> list[str]:
    """Contrôle d'intégrité d'une sauvegarde : manifeste, empreintes, base SQLite et archives lisibles."""
    problems = []
    try:
        manifest = json.loads((d / "manifeste.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["manifeste absent ou illisible (sauvegarde ancienne ou abîmée)"]
    for name, info in manifest.get("files", {}).items():
        p = d / name
        if not p.is_file():
            problems.append(f"{name} manquant")
        elif p.stat().st_size != info["size"] or _sha256(p) != info["sha256"]:
            problems.append(f"{name} modifié ou abîmé (empreinte différente)")
    for name in ("studio.db", "livrables.zip", "pieces_validees.zip"):
        if name not in manifest.get("files", {}):
            problems.append(f"{name} absent du manifeste")
    if problems:
        return problems
    con = sqlite3.connect(f"file:{d / 'studio.db'}?mode=ro", uri=True)
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            problems.append("base SQLite corrompue")
    except sqlite3.DatabaseError as e:
        problems.append(f"base SQLite illisible ({e})")
    finally:
        con.close()
    for name in ("livrables.zip", "pieces_validees.zip"):
        try:
            with zipfile.ZipFile(d / name) as z:
                bad = z.testzip()
            if bad:
                problems.append(f"{name} : fichier abîmé ({bad})")
        except zipfile.BadZipFile:
            problems.append(f"{name} illisible")
    return problems


def verify(cfg, name: str) -> list[str]:
    d = backup_dir(cfg) / name
    if not d.is_dir():
        raise FileNotFoundError(f"Sauvegarde introuvable : {name}")
    return verify_dir(d)


def _force(func, path, exc_info):
    """Suppression d'un fichier en lecture seule (coffre des pièces validées), notamment sous Windows."""
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


def prune(cfg) -> list[str]:
    keep = max(1, int(cfg.get("backup", "keep", default=14)))
    root = backup_dir(cfg)
    if not root.exists():
        return []
    dirs = sorted((d for d in root.iterdir() if d.is_dir() and not d.name.endswith(".en-cours")), key=lambda d: d.name)
    removed = []
    for d in dirs[:-keep]:
        shutil.rmtree(d, onerror=_force)
        removed.append(d.name)
    return removed


def copy_external(cfg, dest: Path) -> Path | None:
    """Copie la sauvegarde dans backup.external_dir (autre disque, OneDrive…) et y garde le même nombre de copies."""
    ext = cfg.get("backup", "external_dir", default="")
    if not ext:
        return None
    root = Path(os.path.expandvars(os.path.expanduser(ext))) / "AI-Enterprise-Studio"
    target = root / dest.name
    tmp = target.with_name(target.name + ".en-cours")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(dest, tmp)
    problems = verify_dir(tmp)
    if problems:
        shutil.rmtree(tmp, ignore_errors=True)
        raise BackupError("copie externe invalide : " + " ; ".join(problems))
    if target.exists():
        shutil.rmtree(target, onerror=_force)
    os.replace(tmp, target)
    keep = max(1, int(cfg.get("backup", "keep", default=14)))
    for d in sorted((d for d in root.iterdir() if d.is_dir() and not d.name.endswith(".en-cours")), key=lambda d: d.name)[:-keep]:
        shutil.rmtree(d, onerror=_force)
    return target


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
        _alert(cfg, db, f"Sauvegarde du jour impossible : {e}")
        return None
    db.kv_set("last_backup_day", today)
    db.log("systeme", "sauvegarde", f"Sauvegarde du jour vérifiée : {dest}")
    try:
        ext = copy_external(cfg, dest)
        if ext:
            db.log("systeme", "sauvegarde", f"Copie externe vérifiée : {ext}")
    except Exception as e:
        db.log("systeme", "sauvegarde", f"Copie externe impossible : {e}")
        _alert(cfg, db, f"Copie externe de la sauvegarde impossible : {e}")
    return dest


def _alert(cfg, db, message: str) -> None:
    try:
        from . import notify
        notify.send(cfg, db, "Sauvegarde : problème", message[:200])
    except Exception:
        pass


def list_backups(cfg) -> list[dict]:
    root = backup_dir(cfg)
    if not root.exists():
        return []
    out = []
    for d in sorted((d for d in root.iterdir() if d.is_dir() and not d.name.endswith(".en-cours")), key=lambda d: d.name, reverse=True):
        size = sum(f.stat().st_size for f in d.iterdir() if f.is_file())
        out.append({"name": d.name, "path": str(d), "size": size, "complete": (d / "manifeste.json").is_file()})
    return out


def engine_running(cfg) -> bool:
    """Le moteur a-t-il fait un cycle il y a moins de 3 minutes ? (restaurer pendant qu'il tourne mélangerait tout)"""
    try:
        con = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT v FROM kv WHERE k='last_cycle_at'").fetchone()
        finally:
            con.close()
        if not row:
            return False
        last = datetime.fromisoformat(row[0])
        return datetime.now(last.tzinfo) - last < timedelta(minutes=3)
    except (sqlite3.Error, ValueError, OSError):
        return False


def restore(cfg, name: str, force: bool = False) -> dict:
    """Restauration intégrale : base, livrables et coffre des pièces validées. Le moteur doit être arrêté.

    Vérifie la sauvegarde d'abord (rien n'est touché si elle est abîmée), prépare les dossiers à côté, puis échange.
    L'état actuel est conservé dans data/archives/avant-restauration-<date>/."""
    src = backup_dir(cfg) / name
    if not src.is_dir():
        raise FileNotFoundError(f"Sauvegarde introuvable : {name}")
    problems = verify_dir(src)
    if problems:
        raise BackupError(f"Sauvegarde {name} non restaurable : " + " ; ".join(problems))
    if not force and engine_running(cfg):
        raise BackupError("le moteur semble tourner (cycle il y a moins de 3 min) : arrête-le avant de restaurer.")
    data = Path(cfg.data_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    keep = data / "archives" / f"avant-restauration-{stamp}"
    keep.mkdir(parents=True, exist_ok=True)
    # 1) préparation à côté (aucun changement visible tant que tout n'est pas prêt)
    staged = {}
    for zname, live in (("livrables.zip", Path(cfg.workspace)), ("pieces_validees.zip", _vault(cfg))):
        stage = live.with_name(live.name + ".restauration")
        shutil.rmtree(stage, onerror=_force) if stage.exists() else None
        stage.mkdir(parents=True)
        with zipfile.ZipFile(src / zname) as z:
            for member in z.namelist():                  # pas de chemin qui sorte du dossier
                target = (stage / member).resolve()
                if stage.resolve() not in target.parents and target != stage.resolve():
                    raise BackupError(f"{zname} : chemin refusé ({member})")
            z.extractall(stage)
        staged[live] = stage
    for f in _vault_files(staged.get(_vault(cfg))):
        os.chmod(f, stat.S_IREAD)                        # coffre à nouveau en lecture seule
    # 2) base
    db_path = Path(cfg.db_path)
    if db_path.exists():
        shutil.copy2(db_path, keep / "studio.db")
    for ext in ("-wal", "-shm"):
        Path(str(db_path) + ext).unlink(missing_ok=True)
    shutil.copy2(src / "studio.db", db_path)
    # 3) dossiers : l'actuel est mis de côté, le restauré prend sa place
    for live, stage in staged.items():
        if live.exists():
            shutil.move(str(live), str(keep / live.name))
        os.replace(stage, live)
    return {"db": str(db_path), "workspace": str(cfg.workspace), "vault": str(_vault(cfg)), "previous": str(keep)}


def _vault_files(d: Path | None):
    if not d or not d.exists():
        return []
    return [p for p in d.rglob("*") if p.is_file()]
