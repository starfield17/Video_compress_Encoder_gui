from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_msi import build_wix_command, validate_source


ROOT = Path(__file__).resolve().parent.parent


class WindowsMsiTestCase(unittest.TestCase):
    def test_wix_source_is_per_user_and_has_upgrade_and_shortcut_metadata(self) -> None:
        source = (ROOT / "packaging" / "windows" / "Product.wxs").read_text(encoding="utf-8")
        self.assertIn('Scope="perUser"', source)
        self.assertIn("MajorUpgrade", source)
        self.assertIn("StartMenuShortcut", source)
        self.assertNotIn("DesktopFolder", source)
        self.assertIn("ARPPRODUCTICON", source)
        self.assertIn('Include="$(var.SourceDir)\\**"', source)

    def test_build_command_maps_release_architectures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            common = {
                "version": "1.2.3",
                "source_dir": root / "source",
                "output_path": root / "package.msi",
                "intermediate_dir": root / "intermediate",
                "icon_path": root / "app.ico",
                "wix_executable": "wix",
                "wxs_path": ROOT / "packaging" / "windows" / "Product.wxs",
            }
            x64 = build_wix_command(architecture="x86_64", **common)
            arm64 = build_wix_command(architecture="arm64", **common)
        self.assertEqual(x64[x64.index("-arch") + 1], "x64")
        self.assertEqual(arm64[arm64.index("-arch") + 1], "arm64")
        self.assertIn("Version=1.2.3.0", x64)
        self.assertIn("-intermediateFolder", x64)

    def test_source_validation_requires_app_and_bundled_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "dist"
            icon = root / "app.ico"
            source.mkdir()
            icon.write_bytes(b"ico")
            with self.assertRaisesRegex(FileNotFoundError, "MSI source is incomplete"):
                validate_source(source, icon)

            (source / "video-compressor.exe").write_bytes(b"exe")
            ffmpeg_bin = source / "FFmpeg" / "bin"
            ffmpeg_bin.mkdir(parents=True)
            (ffmpeg_bin / "ffmpeg.exe").write_bytes(b"exe")
            (ffmpeg_bin / "ffprobe.exe").write_bytes(b"exe")
            validate_source(source, icon)


if __name__ == "__main__":
    unittest.main(verbosity=2)
