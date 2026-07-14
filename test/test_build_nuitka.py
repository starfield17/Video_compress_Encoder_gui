from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_nuitka import (
    build_nuitka_command,
    build_paths,
    final_package_dir,
    find_ffmpeg_pair,
    normalize_version,
    stage_release_resources,
)


class NuitkaBuildCommandTestCase(unittest.TestCase):
    def test_common_command_contains_required_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            command = build_nuitka_command(
                "1.2.3",
                root=root,
                platform_name="linux",
            )

            self.assertIn("--mode=standalone", command)
            self.assertIn("--enable-plugin=pyside6", command)
            self.assertIn("--include-package=cli", command)
            self.assertIn("--include-package=core", command)
            self.assertIn("--include-package=gui", command)
            self.assertIn("--output-filename=video-compressor", command)
            self.assertTrue(
                any(option.startswith("--report=") for option in command)
            )

    def test_windows_command_contains_metadata_and_console_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            command = build_nuitka_command(
                "1.2.3",
                root=root,
                platform_name="win32",
            )

            self.assertIn("--msvc=latest", command)
            self.assertIn("--windows-console-mode=attach", command)
            self.assertIn("--product-name=Video Compressor", command)
            self.assertIn("--file-description=Video Compressor", command)
            self.assertIn("--company-name=starfield17", command)
            self.assertIn("--product-version=1.2.3.0", command)
            self.assertIn("--file-version=1.2.3.0", command)


class NuitkaVersionTestCase(unittest.TestCase):
    def test_three_part_version_gets_windows_build_component(self) -> None:
        self.assertEqual(normalize_version("1.2.3"), "1.2.3.0")

    def test_four_part_version_is_unchanged(self) -> None:
        self.assertEqual(normalize_version("1.2.3.4"), "1.2.3.4")

    def test_nonnumeric_version_fails_clearly(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid version"):
            normalize_version("1.2.3-beta")


class NuitkaStagingTestCase(unittest.TestCase):
    def _make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        (root / "config" / "i18n").mkdir(parents=True)
        (root / "config" / "i18n" / "en.json").write_text("{}", encoding="utf-8")
        (root / "README.md").write_text("README", encoding="utf-8")
        (root / "workdir").mkdir()
        (root / "workdir" / "runtime-only.txt").write_text("not bundled", encoding="utf-8")
        package_dir = root / "dist" / "video-compressor"
        package_dir.mkdir(parents=True)
        return temp_dir, root, package_dir

    def test_staging_copies_config_and_readme_but_not_workdir(self) -> None:
        temp_dir, root, package_dir = self._make_root()
        with temp_dir:
            stage_release_resources(package_dir, root=root, platform_name="linux")

            self.assertEqual((package_dir / "config" / "i18n" / "en.json").read_text(encoding="utf-8"), "{}")
            self.assertEqual((package_dir / "README.md").read_text(encoding="utf-8"), "README")
            self.assertFalse((package_dir / "workdir").exists())

    def test_staging_copies_complete_matching_ffmpeg_pair(self) -> None:
        temp_dir, root, package_dir = self._make_root()
        with temp_dir:
            (root / "FFmpeg" / "bin").mkdir(parents=True)
            (root / "FFmpeg" / "bin" / "ffmpeg").write_text("ffmpeg", encoding="utf-8")
            (root / "FFmpeg" / "bin" / "ffprobe").write_text("ffprobe", encoding="utf-8")

            self.assertIsNotNone(find_ffmpeg_pair(root / "FFmpeg", "linux"))
            stage_release_resources(package_dir, root=root, platform_name="linux")

            self.assertTrue((package_dir / "FFmpeg" / "bin" / "ffmpeg").is_file())
            self.assertTrue((package_dir / "FFmpeg" / "bin" / "ffprobe").is_file())

    def test_staging_skips_incomplete_ffmpeg_pair(self) -> None:
        temp_dir, root, package_dir = self._make_root()
        with temp_dir:
            (root / "FFmpeg").mkdir()
            (root / "FFmpeg" / "ffmpeg").write_text("ffmpeg", encoding="utf-8")

            stage_release_resources(package_dir, root=root, platform_name="linux")

            self.assertFalse((package_dir / "FFmpeg").exists())

    def test_staging_skips_wrong_platform_ffmpeg_pair(self) -> None:
        temp_dir, root, package_dir = self._make_root()
        with temp_dir:
            (root / "FFmpeg").mkdir()
            (root / "FFmpeg" / "ffmpeg.exe").write_text("ffmpeg", encoding="utf-8")
            (root / "FFmpeg" / "ffprobe.exe").write_text("ffprobe", encoding="utf-8")

            stage_release_resources(package_dir, root=root, platform_name="linux")

            self.assertFalse((package_dir / "FFmpeg").exists())

    def test_default_public_package_directory_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            expected = root / "dist" / "video-compressor"

            self.assertEqual(
                final_package_dir(root=root),
                expected,
            )
            self.assertEqual(
                build_paths(root=root).package_dir,
                expected,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
