from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.ci_plan import (
    CAP_QUALITY,
    CAP_TESTS,
    CAP_INSTALLER_CONTRACT,
    CAP_PACKAGE_ALL,
    CAP_PACKAGE_WINDOWS_X86_64,
    PACKAGE_TARGETS,
    TEST_TARGETS,
    _match,
    _plan_for_paths,
    _route,
    write_github_outputs,
)

ROOT = Path(__file__).resolve().parent.parent


def _plan(*paths: str) -> dict:
    return _plan_for_paths(list(paths))


def _package_targets(plan: dict) -> list[str]:
    return [row["target"] for row in plan["package_matrix"]]


class MatchTestCase(unittest.TestCase):
    def test_directory_glob_matches_nested_paths(self) -> None:
        self.assertTrue(_match(("core/**",), "core/foo.py"))
        self.assertTrue(_match(("core/**",), "core/a/b/c.py"))
        self.assertTrue(_match(("docs/**",), "docs/a/b/c.md"))
        self.assertFalse(_match(("core/**",), "cli/foo.py"))
        self.assertFalse(_match(("core/**",), "docs/foo.py"))

    def test_plain_file_patterns_are_anchored(self) -> None:
        self.assertTrue(_match(("main.py",), "main.py"))
        self.assertFalse(_match(("main.py",), "core/main.py"))
        self.assertTrue(_match(("scripts/ci_plan.py",), "scripts/ci_plan.py"))
        self.assertFalse(_match(("scripts/ci_plan.py",), "scripts/ci_plan.py.bak"))

    def test_leading_slash_is_tolerated(self) -> None:
        self.assertTrue(_match(("core/**",), "core/foo.py"))


class RouteTestCase(unittest.TestCase):
    def test_docs_only_is_quality_only(self) -> None:
        self.assertEqual(_route("docs/foo.md"), {CAP_QUALITY})
        self.assertEqual(_route("README.md"), {CAP_QUALITY})
        self.assertEqual(_route("AGENTS.md"), {CAP_QUALITY})
        self.assertEqual(_route(".gitignore"), {CAP_QUALITY})

    def test_application_code_runs_all_tests_without_packaging(self) -> None:
        for path in ("main.py", "core/foo.py", "gui/foo.py", "config/foo.py", "test/foo.py"):
            self.assertEqual(_route(path), {CAP_QUALITY, CAP_TESTS}, path)

    def test_windows_installer_route_is_targeted(self) -> None:
        expected = {CAP_QUALITY, CAP_INSTALLER_CONTRACT, CAP_PACKAGE_WINDOWS_X86_64}
        self.assertEqual(_route("packaging/windows/installer.iss"), expected)
        self.assertEqual(_route("packaging/windows/foo.bar"), expected)
        self.assertEqual(_route("scripts/build_setup.py"), expected)

    def test_build_system_changes_request_full_verification(self) -> None:
        expected = {CAP_QUALITY, CAP_TESTS, CAP_INSTALLER_CONTRACT, CAP_PACKAGE_ALL}
        for path in (
            "scripts/build_nuitka.py",
            "scripts/prepare_ffmpeg.py",
            "packaging/ffmpeg/manifest.json",
            "packaging/assets/app.ico",
            "requirements-build.txt",
            "requirements.txt",
            "pyproject.toml",
            "FFmpeg/foo",
            ".github/workflows/ci.yml",
            ".github/workflows/_package.yml",
            "scripts/ci_plan.py",
            "test/test_ci_plan.py",
            "test/test_release_workflows.py",
        ):
            self.assertEqual(_route(path), expected, path)

    def test_unknown_paths_fail_closed(self) -> None:
        expected = {CAP_QUALITY, CAP_TESTS, CAP_INSTALLER_CONTRACT, CAP_PACKAGE_ALL}
        for path in (
            "vmaf/foo.py",
            "runtime/foo.py",
            "models/foo.py",
            "platform/foo.py",
            "something_new/foo.py",
            "unknown.txt",
        ):
            self.assertEqual(_route(path), expected, path)


class PlanTestCase(unittest.TestCase):
    def test_empty_changed_paths_fails_closed_to_full(self) -> None:
        plan = _plan()
        self.assertTrue(plan["has_tests"])
        self.assertTrue(plan["run_installer_contract"])
        self.assertTrue(plan["has_packages"])
        self.assertEqual(len(plan["package_matrix"]), 5)

    def test_canonical_matrices_are_the_single_source_of_truth(self) -> None:
        self.assertEqual(
            {target: (row["runner"], row["architecture"]) for target, row in TEST_TARGETS.items()},
            {
                "linux": ("ubuntu-22.04", "x64"),
                "windows": ("windows-2022", "x64"),
                "macos": ("macos-15", "arm64"),
            },
        )
        self.assertEqual(len(PACKAGE_TARGETS), 5)
        self.assertEqual(PACKAGE_TARGETS["windows-arm64"]["windows_compiler"], "clang")
        self.assertEqual(PACKAGE_TARGETS["windows-x86_64"]["windows_compiler"], "msvc")
        self.assertEqual(PACKAGE_TARGETS["windows-x86_64"]["installer_architecture"], "x86_64")
        self.assertEqual(PACKAGE_TARGETS["windows-arm64"]["installer_architecture"], "arm64")

    def test_docs_only_plan_has_quality_only(self) -> None:
        plan = _plan("docs/foo.md")
        self.assertFalse(plan["has_tests"])
        self.assertFalse(plan["run_installer_contract"])
        self.assertFalse(plan["has_packages"])
        self.assertEqual(plan["test_matrix"], [])
        self.assertEqual(plan["package_matrix"], [])
        self.assertIn("Verification plan", plan["summary"])

    def test_core_change_runs_three_os_tests_without_packages(self) -> None:
        plan = _plan("core/foo.py")
        self.assertTrue(plan["has_tests"])
        self.assertEqual(
            [(row["platform"], row["runner"]) for row in plan["test_matrix"]],
            [
                ("linux", "ubuntu-22.04"),
                ("windows", "windows-2022"),
                ("macos", "macos-15"),
            ],
        )
        self.assertFalse(plan["run_installer_contract"])
        self.assertFalse(plan["has_packages"])
        self.assertEqual(plan["package_mode"], "smoke")

    def test_installer_change_selects_windows_x64_smoke(self) -> None:
        plan = _plan("packaging/windows/installer.iss")
        self.assertFalse(plan["has_tests"])
        self.assertTrue(plan["run_installer_contract"])
        self.assertTrue(plan["has_packages"])
        self.assertEqual(_package_targets(plan), ["windows-x86_64"])
        self.assertEqual(plan["package_mode"], "smoke")

    def test_build_nuitka_change_selects_all_five_smoke_packages(self) -> None:
        plan = _plan("scripts/build_nuitka.py")
        self.assertTrue(plan["run_installer_contract"])
        self.assertTrue(plan["has_packages"])
        self.assertEqual(_package_targets(plan), list(PACKAGE_TARGETS))

    def test_unknown_change_is_full(self) -> None:
        plan = _plan("vmaf/foo.py")
        self.assertTrue(plan["has_tests"])
        self.assertTrue(plan["run_installer_contract"])
        self.assertTrue(plan["has_packages"])
        self.assertEqual(_package_targets(plan), list(PACKAGE_TARGETS))

    def test_planner_is_monotonic(self) -> None:
        cases = [
            (["docs/foo.md", "packaging/windows/installer.iss"], "installer route must win over docs"),
            (["README.md", "scripts/build_nuitka.py"], "full route must win over docs"),
            (["core/foo.py", "packaging/windows/installer.iss"], "installer route must win over core"),
            (["packaging/windows/installer.iss", "scripts/build_nuitka.py"], "full route must win over installer"),
        ]
        for paths, message in cases:
            with self.subTest(paths=paths):
                combined = _plan(*paths)
                for single in paths:
                    one = _plan(single)
                    self.assertTrue(
                        _plan_implies(combined, one),
                        f"{message}: plan for {single} has capability missing from combined plan",
                    )

    def test_union_matches_plan_of_union(self) -> None:
        for first, second in (
            ("docs/foo.md", "packaging/windows/installer.iss"),
            ("README.md", "scripts/build_nuitka.py"),
            ("core/foo.py", "scripts/build_setup.py"),
        ):
            with self.subTest(first=first, second=second):
                combined = _plan(first, second)
                features = _route(first) | _route(second)
                self.assertEqual(combined["has_tests"], CAP_TESTS in features)
                self.assertEqual(combined["run_installer_contract"], CAP_INSTALLER_CONTRACT in features)
                self.assertEqual(combined["has_packages"], bool(features & {CAP_PACKAGE_ALL, CAP_PACKAGE_WINDOWS_X86_64}))

    def test_workflow_refactor_commit_is_classified_full(self) -> None:
        plan = _plan(
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
            ".github/workflows/_package.yml",
            "scripts/ci_plan.py",
            "test/test_ci_plan.py",
        )
        self.assertTrue(plan["has_tests"])
        self.assertTrue(plan["run_installer_contract"])
        self.assertTrue(plan["has_packages"])
        self.assertEqual(_package_targets(plan), list(PACKAGE_TARGETS))


def _plan_implies(greater: dict, smaller: dict) -> bool:
    """Return True if ``greater`` requests every capability ``smaller`` does."""
    if smaller["has_tests"] and not greater["has_tests"]:
        return False
    if smaller["run_installer_contract"] and not greater["run_installer_contract"]:
        return False
    if smaller["has_packages"] and not greater["has_packages"]:
        return False
    if len(_package_targets(smaller)) and not len(_package_targets(greater)):
        return False
    return True


class CliTestCase(unittest.TestCase):
    def test_cli_prints_compact_json_contract(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ci_plan.py"), "core/foo.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        contract = json.loads(result.stdout)
        self.assertIn("has_tests", contract)
        self.assertIn("test_matrix", contract)
        self.assertIn("run_installer_contract", contract)
        self.assertIn("has_packages", contract)
        self.assertIn("package_matrix", contract)
        self.assertIn("package_mode", contract)
        self.assertTrue(contract["has_tests"])
        self.assertFalse(contract["has_packages"])

    def test_cli_receives_multiple_changed_paths_as_exact_arguments(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "ci_plan.py"),
                "core/foo.py",
                "docs/release notes/verification.md",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        contract = json.loads(result.stdout)
        self.assertTrue(contract["has_tests"])
        self.assertFalse(contract["has_packages"])
        self.assertFalse(contract["run_installer_contract"])


class CIWorkflowBoundaryTestCase(unittest.TestCase):
    def test_changed_paths_use_nul_separated_bash_array(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("git diff --name-only -z", workflow)
        self.assertIn("mapfile -d '' changed", workflow)
        self.assertIn('python3 scripts/ci_plan.py "${changed[@]}"', workflow)
        self.assertNotIn("steps.changes.outputs.changed", workflow)
        self.assertNotIn("json.dumps", workflow)


class GithubOutputTestCase(unittest.TestCase):
    def test_outputs_are_written_when_env_vars_are_present(self) -> None:
        plan = _plan("packaging/windows/installer.iss")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            summary = Path(temp_dir) / "summary"
            output.touch()
            summary.touch()
            write_github_outputs(plan, output_path=output, summary_path=summary)
            output_text = output.read_text(encoding="utf-8")
            summary_text = summary.read_text(encoding="utf-8")
            self.assertIn("has_tests=false", output_text)
            self.assertIn("run_installer_contract=true", output_text)
            self.assertIn("has_packages=true", output_text)
            self.assertIn('"target":"windows-x86_64"', output_text)
            self.assertIn("Verification plan", summary_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
