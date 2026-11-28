"""Contrôle qualité des livrables avant livraison : sites (dossier ou archive .zip) et documents (HTML, Markdown).

Sans dépendance ni appel au modèle : vérifications mécaniques et fiables. Les erreurs bloquent la livraison
(liens internes cassés, texte provisoire oublié…) ; les avertissements sont montrés au propriétaire avant sa décision.
"""
from __future__ import annotations

import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, unquote

PLACEHOLDER = re.compile(r"lorem ipsum|\bTODO\b|\bFIXME\b|\bXXX\b|\[\s*(?:à compléter|a completer|insérer|insert)[^\]]*\]|\{\{[^}]*\}\}", re.I)
BLOCKED_EXT = {".js", ".mjs", ".cjs", ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".jar", ".msi", ".scr"}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self._in_title, self.text = "", False, []
        self.meta, self.links, self.imgs_no_alt, self.h1 = {}, [], 0, 0
        self.lang = self.viewport = None
        self.ext_scripts, self.inline_script_chars, self._in_script, self._skip = [], 0, False, 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name:
                self.meta[name] = a.get("content", "")
            if name == "viewport":
                self.viewport = a.get("content", "")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img":
            if not a.get("alt", "").strip() and a.get("alt") is None:
                self.imgs_no_alt += 1
            if a.get("src"):
                self.links.append(("img", a["src"]))
        elif tag == "a" and a.get("href"):
            self.links.append(("a", a["href"]))
        elif tag == "link" and a.get("href"):
            self.links.append(("link", a["href"]))
        elif tag == "script":
            self._in_script = True
            if a.get("src"):
                self.ext_scripts.append(a["src"]); self.links.append(("script", a["src"]))
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script":
            self._in_script = False
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_script:
            self.inline_script_chars += len(data)
        elif not self._skip:
            self.text.append(data)


def _words(text: str) -> int:
    return len(re.findall(r"\w+", text))


def _parse(html: str) -> _Page:
    p = _Page()
    try:
        p.feed(html)
    except Exception:
        pass
    return p


def _internal(href: str) -> str | None:
    """Chemin local visé par un lien, ou None s'il est externe (http, mailto, tel, ancre…)."""
    u = urlparse(href.strip())
    if u.scheme or href.startswith(("//", "#", "data:")) or not u.path:
        return None
    return unquote(u.path)


def check_site(files: dict[str, bytes]) -> dict:
    """files : chemin relatif (posix) → contenu. Renvoie {errors, warnings, info}."""
    errors, warnings, info = [], [], []
    pages = {k: v for k, v in files.items() if k.lower().endswith((".html", ".htm"))}
    if not pages:
        return {"errors": ["aucune page HTML"], "warnings": [], "info": []}
    if "index.html" not in files:
        errors.append("pas de page d'accueil index.html à la racine")
    for k in files:
        if PurePosixPath(k).suffix.lower() in BLOCKED_EXT:
            errors.append(f"{k} : fichier bloqué par Gmail (mets le JavaScript dans les pages)")
    legal = contact = False
    total_words = 0
    for path, raw in sorted(pages.items()):
        html = raw.decode("utf-8", "replace")
        p = _parse(html)
        text = " ".join(" ".join(p.text).split())
        total_words += _words(text)
        where = f"{path} :"
        if not p.title.strip():
            errors.append(f"{where} pas de balise <title>")
        if not p.meta.get("description"):
            warnings.append(f"{where} pas de meta description (référencement)")
        if not p.viewport:
            warnings.append(f"{where} pas de meta viewport (affichage mobile)")
        if not p.lang:
            warnings.append(f"{where} langue non déclarée (<html lang=\"fr\">)")
        if p.h1 != 1:
            warnings.append(f"{where} {p.h1} titre(s) <h1> (1 attendu)")
        if p.imgs_no_alt:
            warnings.append(f"{where} {p.imgs_no_alt} image(s) sans texte alternatif")
        if _words(text) < 40:
            warnings.append(f"{where} très peu de texte ({_words(text)} mots)")
        for m in set(PLACEHOLDER.findall(text)):
            (errors if re.match(r"lorem|todo|fixme|xxx|\{\{", m, re.I) else warnings).append(f"{where} texte provisoire « {m} »")
        for kind, href in p.links:
            target = _internal(href)
            if target is None:
                if href.startswith(("mailto:", "tel:")):
                    contact = True
                continue
            joined = target.lstrip("/") if target.startswith("/") else f"{PurePosixPath(path).parent.as_posix()}/{target}"
            parts = []
            for seg in joined.split("/"):
                if seg == "..":
                    parts = parts[:-1]
                elif seg not in ("", "."):
                    parts.append(seg)
            resolved = "/".join(parts)
            if not resolved or target.endswith("/"):
                resolved = (resolved + "/" if resolved else "") + "index.html"
            if resolved not in files and resolved + "/index.html" not in files:
                errors.append(f"{where} lien cassé vers « {href} »")
        if re.search(r"mentions[- ]l[ée]gales", path + " " + text, re.I):
            legal = True
    if not legal:
        warnings.append("aucune page ou section « mentions légales » trouvée (obligatoire pour un site professionnel)")
    if not contact:
        warnings.append("aucun lien de contact (mailto: ou tel:)")
    size = sum(len(v) for v in files.values())
    info.append(f"{len(pages)} page(s), {len(files)} fichier(s), {total_words} mots, {size // 1024} Ko")
    return {"errors": sorted(set(errors)), "warnings": sorted(set(warnings)), "info": info}


def check_document(name: str, text: str) -> dict:
    errors, warnings, info = [], [], []
    is_html = name.lower().endswith((".html", ".htm"))
    body = text
    if is_html:
        p = _parse(text)
        body = " ".join(" ".join(p.text).split())
        if not p.title.strip():
            warnings.append("pas de titre de document (<title>)")
        heads = len(re.findall(r"<h[1-3][\s>]", text, re.I))
        if p.ext_scripts:
            warnings.append("le document charge des scripts externes")
        if re.search(r"<link[^>]+stylesheet[^>]+href=\"(?!https?:)", text, re.I):
            errors.append("feuille de style externe locale : le document ne sera pas mis en forme chez le client (mets le CSS dans la page)")
    else:
        heads = len(re.findall(r"^#{1,3} ", text, re.M))
    words = _words(body)
    if words < 300:
        warnings.append(f"document court ({words} mots)")
    if heads < 2:
        warnings.append("peu ou pas de titres de sections")
    for m in set(PLACEHOLDER.findall(body)):
        (errors if re.match(r"lorem|todo|fixme|xxx|\{\{", m, re.I) else warnings).append(f"texte provisoire « {m} »")
    urls = set(re.findall(r"https?://[^\s\"'<>)]+", text))
    if not urls:
        warnings.append("aucune source citée (URL)")
    info.append(f"{words} mots, {heads} titre(s), {len(urls)} lien(s) ou source(s)")
    return {"errors": errors, "warnings": warnings, "info": info}


def check_path(base: Path, p: Path) -> dict:
    """Contrôle un livrable de l'espace de travail : dossier de site, archive .zip ou document."""
    if p.is_dir():
        files = {f.relative_to(p).as_posix(): f.read_bytes() for f in p.rglob("*") if f.is_file()}
        return check_site(files)
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            root = names[0].split("/")[0] + "/" if names and all(n.startswith(names[0].split("/")[0] + "/") for n in names) else ""
            return check_site({n[len(root):]: z.read(n) for n in names})
    if p.suffix.lower() in (".html", ".htm", ".md", ".txt"):
        return check_document(p.name, p.read_text(encoding="utf-8", errors="replace"))
    return {"errors": [], "warnings": [f"type de fichier non contrôlé ({p.suffix or 'sans extension'})"], "info": []}


def report(r: dict) -> str:
    lines = []
    if r["errors"]:
        lines.append("ERREURS à corriger :\n" + "\n".join("- " + e for e in r["errors"]))
    if r["warnings"]:
        lines.append("Avertissements :\n" + "\n".join("- " + w for w in r["warnings"]))
    lines += r.get("info", [])
    return "\n".join(lines) or "Aucun problème détecté."


def summary(r: dict) -> str:
    """Une ligne pour le propriétaire (fenêtre de validation)."""
    if r["errors"]:
        return f"{len(r['errors'])} erreur(s) : " + " ; ".join(r["errors"][:3])
    if r["warnings"]:
        return f"OK, {len(r['warnings'])} avertissement(s) : " + " ; ".join(r["warnings"][:4])
    return "Aucun problème détecté. " + " ".join(r.get("info", []))
