import ast
import importlib.util
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src"


def imports(path):
    parts = path.relative_to(SOURCE).with_suffix("").parts
    package = ".".join(parts[:-1])
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                yield importlib.util.resolve_name("." * node.level + (node.module or ""), package)
            else:
                yield node.module or ""


class ArchitectureTests(unittest.TestCase):
    def test_business_code_only_depends_on_its_own_contracts_and_pure_stdlib(self):
        pure_stdlib = {
            "__future__",
            "collections",
            "dataclasses",
            "datetime",
            "enum",
            "hashlib",
            "html",
            "logging",
            "re",
            "unicodedata",
            "typing",
        }
        for path in (SOURCE / "dontanello" / "modules").rglob("*.py"):
            parts = path.relative_to(SOURCE).parts
            if "adapters" in parts:
                continue
            own_module = ".".join(parts[:3])
            for dependency in imports(path):
                with self.subTest(file=path.name, dependency=dependency):
                    self.assertTrue(
                        dependency == own_module
                        or dependency.startswith(own_module + ".")
                        or dependency.split(".")[0] in pure_stdlib,
                        f"Business code must not import infrastructure: {dependency}",
                    )

    def test_cross_module_imports_use_public_api(self):
        for path in (SOURCE / "dontanello").rglob("*.py"):
            parts = path.relative_to(SOURCE).parts
            own_module = parts[2] if len(parts) > 3 and parts[1] == "modules" else None
            is_composition_root = len(parts) > 1 and parts[1] == "bootstrap"
            for dependency in imports(path):
                target = dependency.split(".")
                if target[:2] != ["dontanello", "modules"] or len(target) < 3:
                    continue
                if target[2] != own_module and not is_composition_root:
                    self.assertEqual(len(target), 3, f"Private module import: {path}: {dependency}")
