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

    def test_integrations_and_platform_do_not_depend_on_application(self):
        for folder in ("integrations", "platform"):
            for path in (SOURCE / "dontanello" / folder).rglob("*.py"):
                for dependency in imports(path):
                    self.assertFalse(
                        dependency.startswith(
                            ("dontanello.modules", "dontanello.entrypoints", "dontanello.bootstrap")
                        ),
                        f"{path}: {dependency}",
                    )

    def test_cross_module_imports_use_public_api(self):
        for path in (SOURCE / "dontanello").rglob("*.py"):
            parts = path.relative_to(SOURCE).parts
            own_module = parts[2] if len(parts) > 3 and parts[1] == "modules" else None
            for dependency in imports(path):
                target = dependency.split(".")
                if target[:2] != ["dontanello", "modules"] or len(target) < 3:
                    continue
                if target[2] != own_module and path.name != "bootstrap.py":
                    self.assertEqual(len(target), 3, f"Private module import: {path}: {dependency}")

    def test_package_import_graph_has_no_cycles(self):
        graph = {}
        for path in (SOURCE / "dontanello").rglob("*.py"):
            parts = list(path.relative_to(SOURCE).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            graph[".".join(parts)] = set(imports(path))
        visiting, visited = set(), set()

        def visit(module):
            self.assertNotIn(module, visiting, f"Import cycle at {module}")
            if module in visited:
                return
            visiting.add(module)
            for dependency in graph[module]:
                if dependency in graph:
                    visit(dependency)
            visiting.remove(module)
            visited.add(module)

        for module in graph:
            visit(module)
