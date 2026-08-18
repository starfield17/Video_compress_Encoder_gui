from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from scripts.ci_plan import PACKAGE_TARGETS, TEST_TARGETS

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

RELEASE_EXPECTED_PACKAGES = [
    "video-compressor-{release}-windows-x86_64.zip",
    "video-compressor-{release}-windows-x86_64-setup.exe",
    "video-compressor-{release}-windows-arm64.zip",
    "video-compressor-{release}-windows-arm64-setup.exe",
    "video-compressor-{release}-linux-x86_64.tar.gz",
    "video-compressor-{release}-linux-arm64.tar.gz",
    "video-compressor-{release}-macos-x86_64.tar.gz",
    "video-compressor-{release}-macos-arm64.tar.gz",
    "video-compressor-{release}-macos-x86_64.dmg",
    "video-compressor-{release}-macos-arm64.dmg",
]


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _matrix_rows(workflow: str) -> dict[str, dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in workflow.splitlines():
        start = re.match(r"^\s+- target:\s*(.+?)\s*$", line)
        if start:
            current = {"target": start.group(1)}
            rows.append(current)
        if current is None:
            continue
        match = re.match(r"^\s{12}([a-z_]+):\s*(.+?)\s*$", line)
        if match:
            current[match.group(1)] = match.group(2)
    return {row["target"]: row for row in rows if "target" in row}


class PlannerContractTestCase(unittest.TestCase):
    def test_planner_is_the_single_source_of_truth_for_targets(self) -> None:
        self.assertEqual(set(TEST_TARGETS), {"linux", "windows", "macos"})
        self.assertEqual(
            set(PACKAGE_TARGETS),
            {
                "windows-x86_64",
                "windows-arm64",
                "linux-x86_64",
                "linux-arm64",
                "macos-x86_64",
                "macos-arm64",
            },
        )


class WorkflowLayoutTestCase(unittest.TestCase):
    def test_three_entry_workflows_and_four_reusable_workflows(self) -> None:
        expected_entries = {"ci.yml", "verify.yml", "release.yml"}
        expected_reusables = {
            "_quality.yml",
            "_test.yml",
            "_windows-installer-contract.yml",
            "_package.yml",
        }
        files = {path.name for path in WORKFLOWS.iterdir()}
        self.assertTrue(expected_entries <= files)
        self.assertTrue(expected_reusables <= files)


class CIWorkflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = _workflow("ci.yml")

    def test_ci_uses_reusable_workflows(self) -> None:
        for name in (
            "_quality.yml",
            "_test.yml",
            "_windows-installer-contract.yml",
            "_package.yml",
        ):
            self.assertIn(f"uses: ./.github/workflows/{name}", self.workflow, name)

    def test_ci_has_plan_job(self) -> None:
        self.assertIn("plan:", self.workflow)
        self.assertIn("scripts/ci_plan.py", self.workflow)

    def test_ci_has_ci_gate(self) -> None:
        self.assertIn("gate:", self.workflow)
        self.assertIn("name: CI Gate", self.workflow)
        self.assertIn("if: always()", self.workflow)

    def test_ci_does_not_embed_six_target_matrix(self) -> None:
        # The six native targets live in the planner, not in ci.yml.
        self.assertEqual(_matrix_rows(self.workflow), {})

    def test_ci_passes_planner_matrix_into_matrix(self) -> None:
        self.assertIn("include: ${{ fromJSON(needs.plan.outputs.test_matrix) }}", self.workflow)
        self.assertIn("include: ${{ fromJSON(needs.plan.outputs.package_matrix) }}", self.workflow)

    def test_ci_gate_validates_planner_contract(self) -> None:
        self.assertIn("needs: [plan, quality, tests, installer, packages]", self.workflow)
        self.assertIn('"${{ needs.tests.result }}" != "skipped"', self.workflow)
        self.assertIn('"${{ needs.packages.result }}" != "skipped"', self.workflow)


class VerifyWorkflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = _workflow("verify.yml")

    def test_verify_has_workflow_dispatch_with_profiles(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("profile:", self.workflow)
        for profile in ("fast", "targeted", "installer", "full"):
            self.assertIn(f"- {profile}", self.workflow)

    def test_verify_has_ten_inputs(self) -> None:
        inputs = [
            "test_linux",
            "test_windows",
            "test_macos",
            "package_windows_x86_64",
            "package_windows_arm64",
            "package_linux_x86_64",
            "package_linux_arm64",
            "package_macos_x86_64",
            "package_macos_arm64",
        ]
        for name in inputs:
            self.assertIn(name + ":", self.workflow)
        # "profile" plus the nine native toggles.
        self.assertEqual(self.workflow.count("type: boolean"), 9)

    def test_verify_uses_reusable_workflows_and_gate(self) -> None:
        for name in (
            "_quality.yml",
            "_test.yml",
            "_windows-installer-contract.yml",
            "_package.yml",
        ):
            self.assertIn(f"uses: ./.github/workflows/{name}", self.workflow, name)
        self.assertIn("Verify Gate", self.workflow)


class ReleaseWorkflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = _workflow("release.yml")

    def test_release_gates_on_tag(self) -> None:
        self.assertIn("on:", self.workflow)
        self.assertIn('"v*"', self.workflow)

    def test_release_uses_reusable_workflows_with_mode_release(self) -> None:
        for name in (
            "_quality.yml",
            "_test.yml",
            "_windows-installer-contract.yml",
            "_package.yml",
        ):
            self.assertIn(f"uses: ./.github/workflows/{name}", self.workflow, name)
        self.assertIn("mode: release", self.workflow)
        self.assertIn("secrets: inherit", self.workflow)
        self.assertIn("ref: ${{ github.ref }}", self.workflow)

    def test_release_matrix_has_six_native_targets(self) -> None:
        rows = _matrix_rows(self.workflow)
        self.assertEqual(set(rows), set(PACKAGE_TARGETS))
        for target, expected in PACKAGE_TARGETS.items():
            self.assertEqual(rows[target]["runner"], expected["runner"])
            self.assertEqual(rows[target]["architecture"], expected["architecture"])
            self.assertEqual(rows[target]["package_kind"], expected["package_kind"])
        self.assertEqual(rows["windows-x86_64"]["windows_compiler"], "msvc")
        self.assertEqual(rows["windows-arm64"]["windows_compiler"], "clang")
        self.assertEqual(rows["windows-x86_64"]["installer_architecture"], "x86_64")
        self.assertEqual(rows["windows-arm64"]["installer_architecture"], "arm64")

    def test_release_preflight_requires_all_prerequisites(self) -> None:
        self.assertIn("preflight:", self.workflow)
        self.assertIn("needs: [quality, tests, installer]", self.workflow)
        self.assertIn("needs.preflight.result == 'success'", self.workflow)

    def test_release_does_not_embed_installer_contract(self) -> None:
        # The installer contract is a reusable workflow call, not an inline job.
        self.assertNotIn("windows-installer-contract:", self.workflow)

    def test_release_publish_preserves_10_package_contract(self) -> None:
        self.assertIn("publish:", self.workflow)
        self.assertIn("Expected 10 release packages", self.workflow)
        self.assertIn("packages/*.dmg", self.workflow)
        self.assertIn("packages/*-setup.exe", self.workflow)
        self.assertIn("sha256sum *.zip *.tar.gz *.dmg *-setup.exe", self.workflow)
        self.assertIn("pattern: release-package-*", self.workflow)

    def test_release_setup_upload_glob_covers_setup_exe(self) -> None:
        # The Windows Setup upload glob must live in _package.yml and match
        # both the generic target archive and the -setup.exe file.
        package = _workflow("_package.yml")
        self.assertIn("video-compressor-${{ github.ref_name }}-${{ inputs.target }}-setup.exe", package)
        self.assertIn("video-compressor-${{ github.ref_name }}-${{ inputs.target }}.*", package)

    def test_release_expected_packages_are_ten(self) -> None:
        self.assertEqual(len(RELEASE_EXPECTED_PACKAGES), 10)
        self.assertEqual(len({name.format(release="x") for name in RELEASE_EXPECTED_PACKAGES}), 10)


class QualityWorkflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = _workflow("_quality.yml")

    def test_quality_runs_on_ubuntu_with_python_313(self) -> None:
        self.assertIn("runs-on: ubuntu-22.04", self.workflow)
        self.assertIn('python-version: "3.13"', self.workflow)

    def test_quality_runs_contract_checks_and_lint(self) -> None:
        for test in (
            "test_architecture.py",
            "test_windows_setup.py",
            "test_release_workflows.py",
            "test_ci_plan.py",
        ):
            self.assertIn(f'-p "{test}"', self.workflow)
        self.assertIn("ruff check .", self.workflow)
        self.assertIn("pyright", self.workflow)
        self.assertIn("python -m compileall -q main.py cli core gui scripts test", self.workflow)
        self.assertIn("python scripts/build_icons.py --check", self.workflow)


class TestWorkflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = _workflow("_test.yml")

    def test_test_workflow_has_required_inputs(self) -> None:
        for name in ("runner", "platform", "architecture"):
            self.assertIn(f"{name}:", self.workflow)
            self.assertIn(f"${{{{ inputs.{name} }}}}", self.workflow)

    def test_test_workflow_does_not_do_packaging(self) -> None:
        self.assertNotIn("Nuitka", self.workflow)
        self.assertNotIn("build_nuitka", self.workflow)
        self.assertNotIn("prepare_ffmpeg", self.workflow)
        self.assertNotIn("ISCC", self.workflow)
        self.assertNotIn("build_setup", self.workflow)

    def test_test_workflow_runs_full_unit_suite(self) -> None:
        self.assertIn("python -m unittest discover -s test -p \"test_*.py\" -v", self.workflow)
        self.assertIn("python main.py --cli --help", self.workflow)


class InstallerContractWorkflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = _workflow("_windows-installer-contract.yml")

    def test_inno_setup_pin_is_preserved(self) -> None:
        self.assertIn("innosetup-7.1.0-x64.exe", self.workflow)
        self.assertIn("/PORTABLE=1", self.workflow)
        self.assertIn("INNO_ISCC", self.workflow)

    def test_contract_compiles_both_architectures(self) -> None:
        self.assertIn("x86_64", self.workflow)
        self.assertIn("arm64", self.workflow)
        self.assertGreaterEqual(self.workflow.count("scripts\\build_setup.py"), 1)
        self.assertIn('--iscc "$env:INNO_ISCC"', self.workflow)
        self.assertIn("installer-contract-source", self.workflow)
        self.assertIn("runs-on: windows-2022", self.workflow)


class PackageWorkflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = _workflow("_package.yml")

    def test_package_workflow_declares_modes(self) -> None:
        self.assertIn("mode:", self.workflow)
        self.assertIn("smoke", self.workflow)
        self.assertIn("candidate", self.workflow)
        self.assertIn("release", self.workflow)

    def test_package_workflow_has_required_inputs(self) -> None:
        for name in (
            "target",
            "runner",
            "architecture",
            "package_kind",
            "windows_compiler",
            "installer_architecture",
            "target_arch",
        ):
            self.assertIn(f"inputs.{name}", self.workflow)

    def test_package_workflow_optional_windows_secrets(self) -> None:
        self.assertIn("WINDOWS_CERTIFICATE_BASE64:", self.workflow)
        self.assertIn("WINDOWS_CERTIFICATE_PASSWORD:", self.workflow)

    def test_package_workflow_builds_real_nuitka_and_setup(self) -> None:
        self.assertIn("scripts/build_nuitka.py", self.workflow)
        self.assertIn("scripts\\build_setup.py", self.workflow)
        self.assertIn("--macos-app-bundle", self.workflow)
        self.assertIn("--target-arch", self.workflow)
        self.assertIn("innosetup-7.1.0-x64.exe", self.workflow)
        self.assertIn("/PORTABLE=1", self.workflow)
        self.assertIn("INNO_ISCC", self.workflow)

    def test_package_workflow_pinned_ffmpeg_behavior(self) -> None:
        self.assertIn("prepare_ffmpeg.py", self.workflow)
        self.assertIn("--require-ffmpeg", self.workflow)
        self.assertIn("hashFiles('packaging/ffmpeg/manifest.json')", self.workflow)

    def test_package_workflow_release_uploads(self) -> None:
        self.assertIn("release-package-${{ inputs.target }}", self.workflow)
        self.assertIn("-setup.exe", self.workflow)
        self.assertIn("video-compressor-${{ github.ref_name }}-${{ inputs.target }}.*", self.workflow)
        self.assertIn("video-compressor-${{ github.ref_name }}-${{ inputs.target }}-setup.exe", self.workflow)

    def test_package_workflow_preserves_windows_smokes(self) -> None:
        self.assertIn("Setup silent install", self.workflow)
        self.assertIn("Setup silent uninstall", self.workflow)
        self.assertIn("unins*.exe", self.workflow)

    def test_package_workflow_preserves_macos_validation(self) -> None:
        self.assertIn("codesign --verify --deep --strict", self.workflow)
        self.assertIn("lipo -archs", self.workflow)
        self.assertIn("hdiutil verify", self.workflow)

    def test_package_workflow_preserves_nuitka_report_upload(self) -> None:
        self.assertIn("nuitka-report-${{ inputs.target }}", self.workflow)
        self.assertIn("build/reports/**", self.workflow)


class PlannerOutputJsonTestCase(unittest.TestCase):
    def test_planner_outputs_parse_as_json_contract(self) -> None:
        # The planner contract must be consumable by fromJSON in matrix jobs.
        from scripts.ci_plan import _plan_for_paths

        plan = _plan_for_paths(["packaging/windows/installer.iss"])
        self.assertIsInstance(json.loads(json.dumps(plan["test_matrix"])), list)
        self.assertIsInstance(json.loads(json.dumps(plan["package_matrix"])), list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
