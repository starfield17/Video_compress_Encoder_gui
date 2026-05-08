from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from core.encoder_capability_cache import (
    ENCODER_CAPABILITIES_SCHEMA_VERSION,
    detect_encoder_capabilities,
    is_encoder_capability_cache_valid,
)
from core.encoder_caps import resolve_encoder
from core.models import BackendChoice, CodecChoice


def _capabilities(hevc: list[tuple[BackendChoice, str]], av1: list[tuple[BackendChoice, str]] | None = None) -> dict:
    return {
        "codecs": {
            "hevc": [{"backend": backend.value, "encoder": encoder_name} for backend, encoder_name in hevc],
            "av1": [{"backend": backend.value, "encoder": encoder_name} for backend, encoder_name in (av1 or [])],
        }
    }


def _cache_payload(**overrides) -> dict:
    payload = {
        "schema_version": ENCODER_CAPABILITIES_SCHEMA_VERSION,
        "ffmpeg_path": "/tmp/ffmpeg",
        "ffmpeg_mtime_ns": 123,
        "ffmpeg_version": "ffmpeg version test",
        "codecs": {"hevc": [], "av1": []},
    }
    payload.update(overrides)
    return payload


class RuntimeCapabilityResolveTestCase(unittest.TestCase):
    def test_auto_uses_runtime_capabilities_before_encoder_listing(self) -> None:
        capabilities = _capabilities(
            [(BackendChoice.QSV, "hevc_qsv"), (BackendChoice.CPU, "libx265")],
        )
        encoder = resolve_encoder(
            CodecChoice.HEVC,
            BackendChoice.AUTO,
            {"hevc_nvenc", "hevc_qsv", "libx265"},
            Path("/tmp/ffmpeg"),
            runtime_capabilities=capabilities,
        )
        self.assertEqual(encoder.backend, BackendChoice.QSV)
        self.assertEqual(encoder.encoder_name, "hevc_qsv")

    def test_runtime_capabilities_are_codec_specific(self) -> None:
        capabilities = _capabilities(
            [(BackendChoice.QSV, "hevc_qsv")],
            [(BackendChoice.CPU, "libsvtav1")],
        )
        encoder = resolve_encoder(
            CodecChoice.AV1,
            BackendChoice.AUTO,
            {"av1_qsv", "libsvtav1"},
            Path("/tmp/ffmpeg"),
            runtime_capabilities=capabilities,
        )
        self.assertEqual(encoder.backend, BackendChoice.CPU)
        self.assertEqual(encoder.encoder_name, "libsvtav1")

    def test_concrete_backend_uses_runtime_smoke_test_result(self) -> None:
        capabilities = _capabilities([(BackendChoice.QSV, "hevc_qsv")])
        with self.assertRaisesRegex(RuntimeError, "not usable"):
            resolve_encoder(
                CodecChoice.HEVC,
                BackendChoice.NVENC,
                {"hevc_nvenc"},
                Path("/tmp/ffmpeg"),
                runtime_capabilities=capabilities,
            )


class EncoderCapabilityCacheTestCase(unittest.TestCase):
    def test_cache_validation_uses_ffmpeg_fingerprint(self) -> None:
        with (
            patch("core.encoder_capability_cache._ffmpeg_mtime_ns", return_value=123),
            patch("core.encoder_capability_cache._ffmpeg_version_line", return_value="ffmpeg version test"),
        ):
            self.assertTrue(is_encoder_capability_cache_valid(_cache_payload(), Path("/tmp/ffmpeg")))
            self.assertFalse(
                is_encoder_capability_cache_valid(
                    _cache_payload(ffmpeg_path="/tmp/other-ffmpeg"),
                    Path("/tmp/ffmpeg"),
                )
            )
            self.assertFalse(
                is_encoder_capability_cache_valid(
                    _cache_payload(ffmpeg_mtime_ns=456),
                    Path("/tmp/ffmpeg"),
                )
            )
            self.assertFalse(
                is_encoder_capability_cache_valid(
                    _cache_payload(ffmpeg_version="ffmpeg version old"),
                    Path("/tmp/ffmpeg"),
                )
            )

    def test_detection_filters_encoder_listing_with_smoke_tests(self) -> None:
        def fake_smoke_test(_ffmpeg_path: Path, encoder_name: str) -> bool:
            return encoder_name in {"hevc_qsv", "libx265", "libsvtav1"}

        with (
            patch("core.encoder_capability_cache._ffmpeg_mtime_ns", return_value=123),
            patch("core.encoder_capability_cache._ffmpeg_version_line", return_value="ffmpeg version test"),
            patch("core.encoder_capability_cache.smoke_test_encoder", side_effect=fake_smoke_test),
        ):
            capabilities = detect_encoder_capabilities(
                Path("/tmp/ffmpeg"),
                available_encoders={"hevc_nvenc", "hevc_qsv", "libx265", "libsvtav1"},
            )

        self.assertEqual(
            capabilities["codecs"]["hevc"],
            [
                {"backend": "qsv", "encoder": "hevc_qsv"},
                {"backend": "cpu", "encoder": "libx265"},
            ],
        )
        self.assertEqual(
            capabilities["codecs"]["av1"],
            [{"backend": "cpu", "encoder": "libsvtav1"}],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
