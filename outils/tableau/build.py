"""Reconstruit ai_studio/web/index.html à partir de index_source.html et des icônes de la maquette (icons.json).

Usage : python outils/tableau/build.py   (après toute modification de index_source.html)
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
src = (HERE / "index_source.html").read_text(encoding="utf-8")
icons = json.loads((HERE / "icons.json").read_text(encoding="utf-8"))
out = HERE.parent.parent / "ai_studio" / "web" / "index.html"
out.write_text(src.replace("__ICONS__", json.dumps(icons, ensure_ascii=False)), encoding="utf-8")
print(f"Écrit : {out}")
