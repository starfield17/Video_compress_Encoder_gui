from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.analysis_runtime import AnalysisCapabilities, format_analysis_capability_report
from core.vmaf_runtime import VMAF_PRODUCTION_MODELS
from scripts.prepare_ffmpeg import (
    binary_architecture,
    load_manifest,
    prepare_target,
    verify_capabilities,
)


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TARGETS = {
    "windows-x86_64",
    "windows-arm64",
    "linux-x86_64",
    "linux-arm64",
    "macos-arm64",
}
OWNED_RELEASE_URL_PREFIX = (
    "https://github.com/starfield17/ffmpeg-vmaf-v1-builds/releases/download/"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pe_binary(machine: int) -> bytes:
    payload = bytearray(512)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, machine)
    return bytes(payload)


class FFmpegManifestTestCase(unittest.TestCase):
    def test_script_entrypoint_imports_from_an_isolated_interpreter(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(ROOT / "scripts" / "prepare_ffmpeg.py"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_manifest_pins_all_release_targets(self) -> None:
        manifest = load_manifest(ROOT / "packaging" / "ffmpeg" / "manifest.json")
        self.assertEqual(manifest["ffmpeg_version"], "9.0.1")
        self.assertEqual(set(manifest["targets"]), EXPECTED_TARGETS)
        self.assertNotIn("macos-x86_64", manifest["targets"])
        for target in manifest["targets"].values():
            self.assertTrue(target["archives"])
            for archive in target["archives"]:
                self.assertRegex(archive["sha256"], r"^[0-9a-f]{64}$")
                self.assertNotIn("/latest/", archive["url"])
                self.assertTrue(
                    archive["url"].startswith(OWNED_RELEASE_URL_PREFIX),
                    archive["url"],
                )


class FFmpegPreparationTestCase(unittest.TestCase):
    def _fixture_manifest(self, root: Path, *, archive_digest: str | None = None) -> dict[str, object]:
        archive = root / "fixture.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("../../unsafe/ffmpeg.exe", _pe_binary(0x8664))
            package.writestr("nested/bin/ffprobe.exe", _pe_binary(0x8664))

        license_path = root / "LICENSE.md"
        license_path.write_text("license", encoding="utf-8")
        copying_path = root / "COPYING.GPLv3"
        copying_path.write_text("copying", encoding="utf-8")
        return {
            "schema_version": 1,
            "ffmpeg_version": "9.0.1",
            "licenses": [
                {
                    "name": license_path.name,
                    "url": license_path.as_uri(),
                    "sha256": _sha256(license_path),
                },
                {
                    "name": copying_path.name,
                    "url": copying_path.as_uri(),
                    "sha256": _sha256(copying_path),
                },
            ],
            "targets": {
                "windows-x86_64": {
                    "platform": "windows",
                    "architecture": "x86_64",
                    "provider": "fixture",
                    "source_version": "fixture",
                    "build_recipe": "https://example.invalid/build",
                    "ffmpeg_source": "https://example.invalid/source",
                    "archives": [
                        {
                            "url": archive.as_uri(),
                            "sha256": archive_digest or _sha256(archive),
                            "format": "zip",
                        }
                    ],
                }
            },
        }

    def test_prepares_pair_licenses_and_source_metadata_inside_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "workdir").mkdir()
            manifest = self._fixture_manifest(root)
            output = root / "workdir" / "ffmpeg" / "windows-x86_64"

            prepare_target(
                "windows-x86_64",
                output,
                manifest=manifest,
                root=root,
                run_capability_checks=False,
            )

            ffmpeg = output / "bin" / "ffmpeg.exe"
            ffprobe = output / "bin" / "ffprobe.exe"
            self.assertEqual(binary_architecture(ffmpeg), "x86_64")
            self.assertEqual(binary_architecture(ffprobe), "x86_64")
            self.assertTrue((output / "LICENSES" / "LICENSE.md").is_file())
            self.assertTrue((output / "LICENSES" / "COPYING.GPLv3").is_file())
            source = json.loads((output / "SOURCE.json").read_text(encoding="utf-8"))
            self.assertEqual(source["target"], "windows-x86_64")
            self.assertFalse((root / "unsafe").exists())

    def test_rejects_output_outside_project_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "workdir").mkdir()
            with self.assertRaisesRegex(ValueError, "inside"):
                prepare_target(
                    "windows-x86_64",
                    root / "outside",
                    manifest=self._fixture_manifest(root),
                    root=root,
                    run_capability_checks=False,
                )

    def test_checksum_mismatch_fails_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "workdir").mkdir()
            manifest = self._fixture_manifest(root, archive_digest="0" * 64)
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                prepare_target(
                    "windows-x86_64",
                    root / "workdir" / "output",
                    manifest=manifest,
                    root=root,
                    run_capability_checks=False,
                )

    def test_optional_analysis_capabilities_are_reported_not_required(self) -> None:
        report = format_analysis_capability_report(
            AnalysisCapabilities(
                libvmaf=True,
                libvmaf_cuda=False,
                loopback_decoder=False,
                hwaccels=frozenset(),
                filters=frozenset({"libvmaf"}),
                encoders=frozenset({"libx265"}),
                scale_vt=False,
                scale_cuda=False,
                videotoolbox_hwaccel=False,
                cuda_hwaccel=False,
                videotoolbox_prio_speed=False,
            )
        )
        self.assertIn("CPU VMAF", report)
        self.assertIn("CUDA VMAF filter (not enabled for v1)", report)
        self.assertIn("Loopback decoder", report)

    def test_capability_verification_smokes_all_v1_models_with_shared_builder(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str]) -> str:
            commands.append(command)
            if "-version" in command:
                return "--enable-gpl --enable-version3"
            if "-filters" in command:
                return "libvmaf"
            if "-encoders" in command:
                return "libx265 libsvtav1"
            if "-filter_complex" in command:
                return "VMAF score: 100.0"
            return ""

        capabilities = AnalysisCapabilities(
            libvmaf=True,
            libvmaf_cuda=False,
            loopback_decoder=False,
            hwaccels=frozenset(),
            filters=frozenset({"libvmaf"}),
            encoders=frozenset({"libx265", "libsvtav1"}),
            scale_vt=False,
            scale_cuda=False,
            videotoolbox_hwaccel=False,
            cuda_hwaccel=False,
            videotoolbox_prio_speed=False,
        )
        with (
            patch("scripts.prepare_ffmpeg._run_checked", side_effect=fake_run),
            patch("scripts.prepare_ffmpeg.detect_analysis_capabilities", return_value=capabilities),
        ):
            verify_capabilities(Path("ffmpeg"), Path("ffprobe"))

        probes = [command for command in commands if "-filter_complex" in command]
        self.assertEqual(len(probes), len(VMAF_PRODUCTION_MODELS))
        for command, model in zip(probes, VMAF_PRODUCTION_MODELS):
            graph = command[command.index("-filter_complex") + 1]
            self.assertIn(model.name, graph)
            self.assertIn(f"scale={model.display_width}:{model.display_height}", graph)
            self.assertIn("format=yuv420p10le", graph)
            self.assertIn("cambi.enc_width=320", graph)
            self.assertIn("cambi.enc_height=180", graph)
            self.assertIn("cambi.enc_bitdepth=8", graph)


if __name__ == "__main__":
    unittest.main(verbosity=2)
