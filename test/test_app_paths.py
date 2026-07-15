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

    def test_macos_app_bundle_uses_resources_and_application_support(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_bundle = root / "Video Compressor.app"
            executable = app_bundle / "Contents" / "MacOS" / "video-compressor"
            resources = app_bundle / "Contents" / "Resources"
            resources_config = resources / "config" / "presets"
            resources_config.mkdir(parents=True)
            (resources_config / "default.json").write_text("default", encoding="utf-8")
            executable.parent.mkdir(parents=True)
            executable.write_text("executable", encoding="utf-8")
            home = root / "home"
            runtime_config = home / "Library" / "Application Support" / "Video Compressor" / "config"
            runtime_config.mkdir(parents=True)
            (runtime_config / "user.json").write_text("user", encoding="utf-8")

            with (
                patch.object(app_paths, "__compiled__", object(), create=True),
                patch.object(sys, "frozen", False, create=True),
                patch.object(sys, "executable", str(executable)),
                patch.object(app_paths.Path, "home", return_value=home),
            ):
                self.assertTrue(app_paths.is_macos_app_bundle())
                self.assertEqual(
                    app_paths.macos_app_bundle_path(),
                    app_bundle.resolve(),
                )
                self.assertEqual(app_paths.bundle_root(), resources.resolve())
                self.assertEqual(
                    app_paths.app_root(),
                    home / "Library" / "Application Support" / "Video Compressor",
                )
                config_dir, workdir = app_paths.ensure_runtime_layout()

            self.assertEqual(config_dir, runtime_config)
            self.assertEqual(
                workdir,
                home / "Library" / "Application Support" / "Video Compressor" / "workdir",
            )
            self.assertEqual((runtime_config / "presets" / "default.json").read_text(encoding="utf-8"), "default")
            self.assertEqual((runtime_config / "user.json").read_text(encoding="utf-8"), "user")
            self.assertFalse((app_bundle / "Contents" / "MacOS" / "config").exists())
            self.assertFalse((app_bundle / "Contents" / "MacOS" / "workdir").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
