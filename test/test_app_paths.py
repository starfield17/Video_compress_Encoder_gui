from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.app_paths as app_paths


class AppPathsCompiledEnvironmentTestCase(unittest.TestCase):
    def test_source_run_uses_repository_root(self) -> None:
        with patch.object(app_paths, "__compiled__", None, create=True), patch.object(sys, "frozen", False, create=True):
            self.assertFalse(app_paths.is_compiled())
            self.assertEqual(app_paths.bundle_root(), app_paths.source_root())
            self.assertEqual(app_paths.app_root(), app_paths.source_root())

    def test_sys_frozen_uses_executable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "video-compressor"
            with (
                patch.object(app_paths, "__compiled__", None, create=True),
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(executable)),
            ):
                self.assertTrue(app_paths.is_compiled())
                self.assertEqual(app_paths.bundle_root(), executable.parent.resolve())
                self.assertEqual(app_paths.app_root(), executable.parent.resolve())

    def test_nuitka_compiled_marker_uses_executable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "video-compressor"
            with (
                patch.object(app_paths, "__compiled__", object(), create=True),
                patch.object(sys, "frozen", False, create=True),
                patch.object(sys, "executable", str(executable)),
            ):
                self.assertTrue(app_paths.is_compiled())
                self.assertEqual(app_paths.bundle_root(), executable.parent.resolve())
                self.assertEqual(app_paths.app_root(), executable.parent.resolve())


if __name__ == "__main__":
    unittest.main(verbosity=2)
