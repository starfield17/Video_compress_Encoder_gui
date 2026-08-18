from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TARGETS = {
    "windows-x86_64": ("windows-2022", "x64", "standalone"),
    "windows-arm64": ("windows-11-arm", "arm64", "standalone"),
    "linux-x86_64": ("ubuntu-22.04", "x64", "standalone"),
    "linux-arm64": ("ubuntu-24.04-arm", "arm64", "standalone"),
    "macos-x86_64": ("macos-15-intel", "x64", "macos-app"),
    "macos-arm64": ("macos-15", "arm64", "macos-app"),
}


def _matrix_rows(workflow: str) -> dict[str, dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in workflow.splitlines():
        start = re.match(r"^\s+- os:\s*(.+?)\s*$", line)
        if start:
            current = {"os": start.group(1)}
            rows.append(current)
        if current is None:
            continue
        match = re.match(r"^\s{12}([a-z_]+):\s*(.+?)\s*$", line)
        if match:
            current[match.group(1)] = match.group(2)
    return {row["target"]: row for row in rows if "target" in row}


class ReleaseWorkflowMatrixTestCase(unittest.TestCase):
    def test_ci_packaging_matrix_has_six_native_targets(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        rows = _matrix_rows(workflow)
        self.assertEqual(set(rows), set(EXPECTED_TARGETS))
        for target, (runner, architecture, package_kind) in EXPECTED_TARGETS.items():
            self.assertEqual(
                (rows[target]["os"], rows[target]["architecture"], rows[target]["package_kind"]),
                (runner, architecture, package_kind),
            )
        self.assertEqual(rows["windows-arm64"]["windows_compiler"], "clang")
        self.assertEqual(rows["windows-x86_64"]["windows_compiler"], "msvc")
        self.assertEqual(rows["windows-x86_64"]["installer_architecture"], "x86_64")
        self.assertEqual(rows["windows-arm64"]["installer_architecture"], "arm64")
        self.assertIn("scripts/build_icons.py --check", workflow)
        self.assertIn("windows-installer-contract:", workflow)
        self.assertIn("needs: [quality, test, windows-installer-contract]", workflow)
        self.assertIn("installer-contract-source", workflow)
        self.assertGreaterEqual(workflow.count("scripts\\build_setup.py"), 2)
        self.assertIn("innosetup-7.1.0-x64.exe", workflow)
        self.assertIn("/PORTABLE=1", workflow)
        self.assertIn("INNO_ISCC", workflow)
        self.assertGreaterEqual(workflow.count('--iscc "$env:INNO_ISCC"'), 2)
        self.assertIn("setup-smoke-installed", workflow)
        self.assertIn("Setup silent install", workflow)
        self.assertIn("Setup silent uninstall", workflow)
        self.assertIn("workdir/tmp", workflow)
        self.assertIn('python-version: "3.13"', workflow)

    def test_release_matrix_and_publish_contract_cover_native_packages(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        rows = _matrix_rows(workflow)
        self.assertEqual(set(rows), set(EXPECTED_TARGETS))
        self.assertIn("architecture: ${{ matrix.architecture }}", workflow)
        self.assertIn("nuitka-report-${{ matrix.target }}", workflow)
        self.assertIn("packages/*.dmg", workflow)
        self.assertIn("packages/*-setup.exe", workflow)
        self.assertIn("Expected 10 release packages", workflow)
        self.assertIn("sha256sum *.zip *.tar.gz *.dmg *-setup.exe", workflow)
        self.assertIn("macos-app-bundle", workflow)
        self.assertIn('tar -czf "video-compressor-${GITHUB_REF_NAME}-${{ matrix.target }}.tar.gz"', workflow)
        self.assertIn("scripts/prepare_ffmpeg.py", workflow)
        self.assertIn("--require-ffmpeg", workflow)
        self.assertIn("windows-installer-contract:", workflow)
        self.assertIn("needs: windows-installer-contract", workflow)
        self.assertIn("installer-contract-source", workflow)
        self.assertGreaterEqual(workflow.count("scripts\\build_setup.py"), 2)
        self.assertIn("scripts\\sign_windows.ps1", workflow)
        self.assertIn("WINDOWS_CERTIFICATE_BASE64", workflow)
        self.assertIn("/PORTABLE=1", workflow)
        self.assertIn("INNO_ISCC", workflow)
        self.assertGreaterEqual(workflow.count('--iscc "$env:INNO_ISCC"'), 2)
        self.assertIn("-setup.exe", workflow)
        self.assertEqual(rows["windows-x86_64"]["installer_architecture"], "x86_64")
        self.assertEqual(rows["windows-arm64"]["installer_architecture"], "arm64")
        self.assertIn('python-version: "3.13"', workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)