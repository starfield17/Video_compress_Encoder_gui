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
        self.assertEqual(rows["windows-x86_64"]["windows_compiler"], "mingw64")

    def test_release_matrix_and_publish_contract_cover_native_packages(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        rows = _matrix_rows(workflow)
        self.assertEqual(set(rows), set(EXPECTED_TARGETS))
        self.assertIn("architecture: ${{ matrix.architecture }}", workflow)
        self.assertIn("nuitka-report-${{ matrix.target }}", workflow)
        self.assertIn("packages/*.dmg", workflow)
        self.assertIn("Expected 8 release packages", workflow)
        self.assertIn("sha256sum *.zip *.tar.gz *.dmg", workflow)
        self.assertIn("macos-app-bundle", workflow)
        self.assertIn('tar -czf "video-compressor-${GITHUB_REF_NAME}-${{ matrix.target }}.tar.gz"', workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
