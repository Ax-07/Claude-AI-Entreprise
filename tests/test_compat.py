"""Compatibilité Python 3.10+ : le propriétaire n'a pas forcément la dernière version."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CompatTest(unittest.TestCase):
    def test_no_backslash_in_fstring_expressions(self):
        # accepté depuis Python 3.12 seulement : sous 3.10/3.11, le moteur ne démarre même pas
        bad = []
        for p in list((ROOT / "ai_studio").rglob("*.py")) + list((ROOT / "tests").glob("*.py")) + list((ROOT / "outils").rglob("*.py")):
            src = p.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.FormattedValue):
                    seg = ast.get_source_segment(src, node.value) or ""
                    if "\\" in seg:
                        bad.append(f"{p.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(bad, [], "f-string avec une barre oblique inverse dans l'expression (refusé avant Python 3.12)")


if __name__ == "__main__":
    unittest.main()
