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

    def test_gui_submodules_do_not_import_main_window(self) -> None:
        violations: list[str] = []
        modules = _app_modules()
        for source, path in modules.items():
            if not source.startswith("gui.") or source == "gui.gui_entry":
                continue
            for imported in _imports(source, path):
                if imported.startswith("gui.gui_mainwindow"):
                    violations.append(f"{path.relative_to(ROOT)} imports {imported!r}")
        self.assertFalse(
            violations,
            "gui.gui_entry is the only GUI entrypoint; other gui sub-modules must not "
            "import MainWindow (it is the composition root):\n" + "\n".join(sorted(violations)),
        )

    def test_queue_state_and_model_do_not_import_queue_view(self) -> None:
        violations: list[str] = []
        modules = _app_modules()
        for source, path in modules.items():
            if source not in {"gui.queue_state", "gui.queue_model"}:
                continue
            for imported in _imports(source, path):
                if imported.startswith(("gui.queue_view", "gui.queue_table")):
                    violations.append(f"{path.relative_to(ROOT)} imports {imported!r}")
        self.assertFalse(
            violations,
            "queue_state is Qt-free and queue_model is the model layer; neither may "
            "depend on the view:\n" + "\n".join(sorted(violations)),
        )

    def test_no_module_imports_removed_queue_table(self) -> None:
        violations: list[str] = []
        modules = _app_modules()
        for source, path in modules.items():
            for imported in _imports(source, path):
                if imported.startswith("gui.queue_table"):
                    violations.append(f"{path.relative_to(ROOT)} imports {imported!r}")
        self.assertFalse(
            violations,
            "gui.queue_table was split into gui.queue_model and gui.queue_view; "
            "importers must be updated:\n" + "\n".join(sorted(violations)),
        )

    def test_smart_sampling_dependency_direction(self) -> None:
        graph = _dependency_graph()
        expected = {
            "core.content_complexity": set(),
            "core.sample_planner": {"core.models"},
            "core.smart_sampling": {
                "core.content_complexity",
                "core.models",
                "core.sample_planner",
            },
        }
        for module, allowed_core_dependencies in expected.items():
            actual = {
                dependency
                for dependency in graph[module]
                if dependency.startswith("core.")
            }
            self.assertEqual(
                actual,
                allowed_core_dependencies,
                f"{module} crossed the Smart sampling module boundary",
            )

    def test_smart_quality_module_boundaries(self) -> None:
        graph = _dependency_graph()
        focused_modules = {
            "core.smart_bitrate",
            "core.smart_cache",
            "core.smart_measurement",
        }
        forbidden_dependencies = {
            "core.smart_quality",
            "core.smart_workflow",
            "core.constraint_resolution",
        }
        violations = {
            module: sorted(graph[module] & forbidden_dependencies)
            for module in focused_modules
            if graph[module] & forbidden_dependencies
        }
        self.assertFalse(
            violations,
            "Focused Smart modules are lower-level owners and may not reach back "
            f"into orchestration or decisions: {violations}",
        )
        self.assertFalse(
            graph["core.smart_workflow"] & {"core.smart_quality", "core.constraint_resolution"},
            "Smart workflow must orchestrate focused modules without importing the "
            "compatibility facade or queue decision policy",
        )

    def test_queue_model_has_no_domain_side_effect_dependencies(self) -> None:
        path = _app_modules()["gui.queue_model"]
        forbidden = (
            "core.analysis_receipts",
            "core.constraint_resolution",
            "core.external_subtitles",
        )
        violations = [
            imported
            for imported in _imports("gui.queue_model", path)
            if imported.startswith(forbidden)
        ]
        self.assertFalse(
            violations,
            "QueueTableModel should emit Qt notifications and delegate record mutations "
            f"to gui.queue_actions, not own domain/file side effects: {violations}",
        )

    def test_options_panel_does_not_probe_ffmpeg(self) -> None:
        path = _app_modules()["gui.encode_options_panel"]
        forbidden_names = {
            "discover_ffmpeg_tools",
            "list_available_encoders",
            "preset_choices_for_encoder",
            "resolve_encoder",
        }
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(
            imported & forbidden_names,
            "EncodeOptionsPanel must render the worker-produced capability snapshot; "
            f"it may not probe FFmpeg synchronously: {sorted(imported & forbidden_names)}",
        )

    def test_main_window_uses_options_panel_public_contract(self) -> None:
        path = _app_modules()["gui.gui_mainwindow"]
        allowed = {
            "analysis_profile_changed",
            "apply_analysis_profile_settings",
            "apply_options",
            "begin_capability_detection",
            "current_analysis_profile_name",
            "notify_capability_detection_failed",
            "read_options",
            "read_preview_options",
            "set_busy",
            "set_runtime_capabilities",
            "set_translator",
            "sync_dependent_controls",
            "validate_parallel_options",
        }
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        used = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == "options_panel"
        }
        self.assertFalse(
            used - allowed,
            "MainWindow must use EncodeOptionsPanel's public contract instead of raw "
            f"widgets: {sorted(used - allowed)}",
        )

    def test_queue_view_depends_on_model(self) -> None:
        view_path = _app_modules().get("gui.queue_view")
        self.assertIsNotNone(view_path)
        self.assertTrue(
            any(
                imported.startswith("gui.queue_model")
                for imported in _imports("gui.queue_view", view_path)
            ),
            "gui.queue_view must use the column definitions from gui.queue_model",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
