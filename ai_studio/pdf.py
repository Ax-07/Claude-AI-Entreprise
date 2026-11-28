"""Conversion HTML → PDF avec le navigateur déjà installé (Microsoft Edge ou Google Chrome, mode sans fenêtre).

Aucune dépendance Python : on appelle le navigateur avec --headless --print-to-pdf. Les agents écrivent le rapport
en HTML (contrôlable, prévisualisable) ; le client reçoit un PDF A4 paginé.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PRINT_CSS = """<style id="ai-studio-print">
@page { size: A4; margin: 16mm 15mm 18mm;
  @bottom-right { content: "Page " counter(page) " / " counter(pages); font: 9pt sans-serif; color: #666; } }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1, h2, h3, h4 { break-after: avoid; page-break-after: avoid; }
table, figure, img, pre, blockquote, li { break-inside: avoid; page-break-inside: avoid; }
p { orphans: 3; widows: 3; }
</style>"""


# Politique de sécurité injectée dans la page imprimée (audit SEC-07) : ni script, ni cadre, ni connexion réseau.
CSP_META = ('<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src file: data:; '
            "style-src 'unsafe-inline' file:; font-src file: data:; script-src 'none'; frame-src 'none'; "
            "object-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'\">")

# Options du navigateur : aucune résolution DNS (donc aucun accès réseau), pas de services annexes.
ISOLATION_FLAGS = ["--host-resolver-rules=MAP * ~NOTFOUND",
                   "--disable-background-networking", "--disable-sync", "--disable-component-update",
                   "--disable-default-apps", "--mute-audio", "--no-service-autorun"]


class PdfError(RuntimeError):
    pass


# ------------------------------------------------------------------ nettoyage du HTML écrit par les agents
DROP_WITH_CONTENT = {"script", "iframe", "frame", "frameset", "object", "applet", "template", "noembed", "noframes"}
DROP_TAG = {"embed", "base", "portal", "param"}
URL_ATTRS = {"src", "href", "poster", "data", "action", "formaction", "background", "xlink:href", "ping", "cite",
             "longdesc", "manifest", "lowsrc", "dynsrc", "srcset", "imagesrcset"}
SAFE_DATA = ("data:image/png", "data:image/jpeg", "data:image/jpg", "data:image/gif", "data:image/webp",
             "data:font/", "data:application/font", "data:application/x-font")


def safe_url(value: str, link: bool = False) -> bool:
    """Lien interne au dossier du livrable (relatif, sans « .. » qui en sort), image en data:, ancre ; pour un lien
    cliquable <a href>, aussi http(s), mailto et tel (le navigateur ne les charge pas)."""
    import posixpath
    import urllib.parse
    v = (value or "").strip()
    if not v or v.startswith("#"):
        return True
    low = v.lower().replace("\t", "").replace("\n", "").replace("\r", "")
    if low.startswith(SAFE_DATA):
        return True
    if link and low.startswith(("http://", "https://", "mailto:", "tel:")):
        return True
    raw = urllib.parse.unquote(urllib.parse.unquote(v))
    if "\\" in raw or raw.startswith("/") or ":" in raw.split("/")[0] or raw.startswith("//"):
        return False                                      # absolu, autre schéma (file:, javascript:…), lecteur Windows
    path = raw.split("#")[0].split("?")[0]
    norm = posixpath.normpath(path) if path else "."
    return not (norm == ".." or norm.startswith("../"))


def _clean_css(css: str) -> str:
    import re
    css = re.sub(r"@import[^;]*;?", "", css, flags=re.I)
    def fix(m):
        url = m.group(2).strip().strip("'\"")
        return m.group(0) if safe_url(url) else "url()"
    css = re.sub(r"url\(\s*(['\"]?)(.*?)\1\s*\)", fix, css, flags=re.I | re.S)
    return re.sub(r"expression\s*\(|behavior\s*:|-moz-binding", "", css, flags=re.I)


def sanitize_html(html_text: str) -> str:
    """Retire scripts, cadres, gestionnaires d'événements et liens vers l'extérieur du dossier du livrable."""
    import html as h
    from html.parser import HTMLParser

    out: list[str] = []
    skip = [0]

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.in_style = False

        def _attrs(self, tag, attrs):
            keep = []
            for k, v in attrs:
                k = (k or "").lower()
                v = v if v is not None else ""
                if k.startswith("on") or k in ("srcdoc", "is", "http-equiv"):
                    continue
                if k in URL_ATTRS:
                    if k in ("srcset", "imagesrcset"):
                        if not all(safe_url(part.strip().split(" ")[0]) for part in v.split(",") if part.strip()):
                            continue
                    elif not safe_url(v, link=(tag == "a" and k == "href")):
                        continue
                if k == "style":
                    v = _clean_css(v)
                keep.append(f' {k}="{h.escape(v, quote=True)}"' if v != "" or k not in URL_ATTRS else "")
            return "".join(keep)

        def _start(self, tag, attrs, close):
            tag = tag.lower()
            if skip[0]:
                if tag in DROP_WITH_CONTENT and not close:
                    skip[0] += 1
                return
            if tag in DROP_WITH_CONTENT:
                if not close:
                    skip[0] = 1
                return
            if tag in DROP_TAG:
                return
            a = dict((k.lower(), v or "") for k, v in attrs)
            if tag == "meta" and "http-equiv" in a:
                return                                    # refresh, CSP concurrente…
            if tag == "link" and not ("stylesheet" in a.get("rel", "").lower() and safe_url(a.get("href", ""))):
                return
            if tag == "style":
                self.in_style = True
            out.append(f"<{tag}{self._attrs(tag, attrs)}{' /' if close else ''}>")

        def handle_starttag(self, tag, attrs):
            self._start(tag, attrs, False)

        def handle_startendtag(self, tag, attrs):
            self._start(tag, attrs, True)

        def handle_endtag(self, tag):
            tag = tag.lower()
            if skip[0]:
                if tag in DROP_WITH_CONTENT:
                    skip[0] -= 1
                return
            if tag in DROP_TAG or tag in DROP_WITH_CONTENT:
                return
            if tag == "style":
                self.in_style = False
            out.append(f"</{tag}>")

        def handle_data(self, data):
            if not skip[0]:
                out.append(_clean_css(data) if self.in_style else data)

        def handle_entityref(self, name):
            if not skip[0]:
                out.append(f"&{name};")

        def handle_charref(self, name):
            if not skip[0]:
                out.append(f"&#{name};")

        def handle_decl(self, decl):
            out.append(f"<!{decl}>")

        def handle_comment(self, data):
            pass                                          # commentaires conditionnels d'Internet Explorer, etc.

        def handle_pi(self, data):
            pass

        def unknown_decl(self, data):
            pass

    p = P()
    p.feed(html_text)
    p.close()
    return "".join(out)


def _candidates() -> list[str]:
    if sys.platform.startswith("win"):
        out = []
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                out += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                        os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
        return out
    if sys.platform == "darwin":
        return ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return [p for n in ("microsoft-edge", "msedge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
            if (p := shutil.which(n))]


def find_browser(cfg=None) -> str | None:
    forced = (cfg.get("delivery", "pdf_browser", default="") if cfg is not None else "") or os.environ.get("PDF_BROWSER", "")
    for c in ([forced] if forced else []) + _candidates():
        if c and Path(c).is_file():
            return c
    return None


def html_to_pdf(cfg, src: Path, dest: Path, timeout: int = 120) -> Path:
    """Imprime `src` (HTML) en PDF dans `dest`. Les fichiers liés (images, CSS) sont résolus depuis le dossier de src."""
    browser = find_browser(cfg)
    if not browser:
        raise PdfError("aucun navigateur pour créer le PDF (Microsoft Edge ou Google Chrome introuvable ; chemin "
                       "réglable dans config.json : delivery.pdf_browser)")
    src, dest = Path(src).resolve(), Path(dest).resolve()
    html = sanitize_html(src.read_text(encoding="utf-8", errors="replace"))      # audit SEC-07
    low = html.lower()
    i = low.find("<head>")
    head = CSP_META + PRINT_CSS
    html = html[:i + 6] + head + html[i + 6:] if i >= 0 else head + html
    tmp = src.with_name(f".impression-{src.stem}.html")         # même dossier : les liens relatifs restent valides
    tmp.write_text(html, encoding="utf-8")
    profile = tempfile.mkdtemp(prefix="ai-studio-pdf-")
    if dest.exists():
        dest.unlink()
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--disable-extensions", "--no-pdf-header-footer", "--hide-scrollbars", f"--user-data-dir={profile}",
           *ISOLATION_FLAGS,
           "--run-all-compositor-stages-before-draw", "--virtual-time-budget=8000", f"--print-to-pdf={dest}", tmp.as_uri()]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # Chrome refuse de tourner en root avec son bac à sable. On ne le désactive plus en silence (audit SEC-07) :
        # il faut l'autoriser explicitement (conteneur dédié), sinon lancer le programme avec un compte ordinaire.
        if not (cfg.get("delivery", "pdf_no_sandbox", default=False) or os.environ.get("PDF_NO_SANDBOX") == "1"):
            tmp.unlink(missing_ok=True); shutil.rmtree(profile, ignore_errors=True)
            raise PdfError("le programme tourne en administrateur (root) : le navigateur ne peut pas utiliser son bac à "
                           "sable. Lance-le avec un compte ordinaire, ou autorise explicitement delivery.pdf_no_sandbox.")
        cmd.insert(1, "--no-sandbox")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise PdfError(f"le navigateur n'a pas fini le PDF en {timeout} s")
    except OSError as e:
        raise PdfError(f"impossible de lancer le navigateur ({e})")
    finally:
        tmp.unlink(missing_ok=True)
        shutil.rmtree(profile, ignore_errors=True)
    if not dest.is_file() or dest.stat().st_size < 1000 or not dest.read_bytes()[:5] == b"%PDF-":
        raise PdfError("le navigateur n'a pas produit de PDF valide" + (f" ({r.stderr.strip()[-300:]})" if r.stderr.strip() else ""))
    return dest


def page_count(path: Path) -> int:
    """Nombre de pages (approximation suffisante pour un contrôle : objets /Type /Page)."""
    import re
    return len(re.findall(rb"/Type\s*/Page(?!s)", Path(path).read_bytes()))
