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


class PdfError(RuntimeError):
    pass


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
    html = src.read_text(encoding="utf-8", errors="replace")
    low = html.lower()
    i = low.find("</head>")
    html = html[:i] + PRINT_CSS + html[i:] if i >= 0 else PRINT_CSS + html
    tmp = src.with_name(f".impression-{src.stem}.html")         # même dossier : les liens relatifs restent valides
    tmp.write_text(html, encoding="utf-8")
    profile = tempfile.mkdtemp(prefix="ai-studio-pdf-")
    if dest.exists():
        dest.unlink()
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--disable-extensions", "--no-pdf-header-footer", "--hide-scrollbars", f"--user-data-dir={profile}",
           "--run-all-compositor-stages-before-draw", "--virtual-time-budget=8000", f"--print-to-pdf={dest}", tmp.as_uri()]
    if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd.insert(1, "--no-sandbox")                            # Chrome refuse de tourner en root sans cette option
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
