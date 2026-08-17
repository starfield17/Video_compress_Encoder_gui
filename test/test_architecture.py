from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP_PACKAGES = ("core", "cli", "gui")
QT_ROOTS = {"PySide2", "PySide6", "PyQt5", "PyQt6", "qtpy", "Qt"}


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT)
    if relative == Path("main.py"):
        return "main"
    if relative.name == "__init__.py":
        return ".".join(relative.parent.parts)
    return ".".join(relative.with_suffix("").parts)


def _app_modules() -> dict[str, Path]:
    paths = [ROOT / "main.py"]
    for package in APP_PACKAGES:
        paths.extend(sorted((ROOT / package).glob("*.py")))
    return {_module_name(path): path for path in paths if path.is_file()}


def _resolve_relative(source: str, level: int, imported: str | None) -> str:
    package_parts = source.split(".")[:-1]
    if level > len(package_parts) + 1:
        return imported or ""
    base = package_parts[: len(package_parts) - level + 1]
    return ".".join((*base, *(imported or "").split("."))).rstrip(".")


def _imports(source: str, path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise AssertionError(f"Cannot inspect {path}: fix its syntax before changing dependencies: {exc}") from exc

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(source, node.level, node.module)
            elif node.module:
                base = node.module
            else:
                continue
            imported_names.append(base)
            # ``from core import models`` depends on the concrete module too;
            # include it when the imported name resolves to an app module.
            imported_names.extend(
                f"{base}.{alias.name}" for alias in node.names if base
            )
    return imported_names


def _top_level(name: str) -> str:
    return name.split(".", 1)[0]


def _is_qt_import(name: str) -> bool:
    top = _top_level(name)
    lower = top.lower()
    return top in QT_ROOTS or lower.startswith(("pyside", "pyqt")) or lower == "qt"


def _dependency_graph() -> dict[str, set[str]]:
    modules = _app_modules()
    graph: dict[str, set[str]] = {name: set() for name in modules}
    for source, path in modules.items():
        for imported in _imports(source, path):
            if imported in modules:
                graph[source].add(imported)
                continue
            # An import of a package itself is represented by its package node;
            # submodule imports are represented by the concrete module when it exists.
            prefix = imported
            while prefix and prefix not in modules:
                prefix = prefix.rpartition(".")[0]
            if prefix:
                graph[source].add(prefix)
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    visiting: list[str] = []
    active: set[str] = set()
    completed: set[str] = set()

    def visit(node: str) -> None:
        if node in active:
            start = visiting.index(node)
            cycles.append([*visiting[start:], node])
            return
        if node in completed:
            return
        active.add(node)
        visiting.append(node)
        for dependency in sorted(graph[node]):
            visit(dependency)
        visiting.pop()
        active.remove(node)
        completed.add(node)

    for node in sorted(graph):
        visit(node)
    return cycles


class ArchitectureTestCase(unittest.TestCase):
    def test_core_has_no_ui_or_entrypoint_dependencies(self) -> None:
        violations: list[str] = []
        modules = _app_modules()
        for source, path in modules.items():
            if not source == "core" and not source.startswith("core."):
                continue
            for imported in _imports(source, path):
                if _top_level(imported) in {"cli", "gui"} or _is_qt_import(imported):
                    violations.append(f"{path.relative_to(ROOT)} imports {imported!r}")
        self.assertFalse(
            violations,
            "core is the reusable, UI-free layer; move this dependency upward or "
            "extract a neutral core abstraction:\n" + "\n".join(sorted(violations)),
        )

    def test_cli_has_no_gui_dependencies(self) -> None:
        violations: list[str] = []
        modules = _app_modules()
        for source, path in modules.items():
            if not source == "cli" and not source.startswith("cli."):
                continue
            for imported in _imports(source, path):
                if _top_level(imported) == "gui":
                    violations.append(f"{path.relative_to(ROOT)} imports {imported!r}")
        self.assertFalse(
            violations,
            "cli must remain usable without the GUI; depend on core or keep UI "
            "selection in main.py:\n" + "\n".join(sorted(violations)),
        )

    def test_app_modules_are_acyclic(self) -> None:
        cycles = _cycles(_dependency_graph())
        rendered = "\n".join(" -> ".join(cycle) for cycle in cycles)
        self.assertFalse(
            cycles,
            "Application modules must form an acyclic dependency graph; move shared "
            "logic into a lower layer to break this cycle:\n" + rendered,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
