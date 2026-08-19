from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.analysis_receipts import ANALYSIS_RECEIPT_SCHEMA_VERSION, load_analysis_receipt
from core.analysis_runtime import (
    COARSE_MAX_CANDIDATES,
    EXACT_MAX_CANDIDATES,
    SOURCE_DECODE_SOFTWARE,
    SOURCE_DECODE_VIDEOTOOLBOX,
    AnalysisCapabilities,
    AnalysisDecodePolicy,
    AnalysisTier,
    VmafBackend,
    build_analysis_execution_plan,
    coarse_encoder_preset,
    cpu_vmaf_plan,
    format_analysis_capability_report,
    help_exposes_loopback_decoder,
    legacy_loopback_plan,
    parse_decoder_names,
    parse_filter_names,
    search_tolerance_bps,
    software_source_plan,
    source_decode_args,
)
from core.build_ffmpeg_cmd import build_video_args
from core.models import (
    AnalysisReceipt,
    BackendChoice,
    CodecChoice,
    EncodeOptions,
    EncodePlanItem,
    EncoderInfo,
    MediaInfo,
    QualityCandidateResult,
    QualitySearchStatus,
    VmafRuntimeSupport,
)
from core.smart_quality import (
    SMART_ANALYSIS_ALGORITHM_VERSION,
    SMART_ANALYSIS_SEMAPHORE,
    SMART_SAMPLE_SCHEME_VERSION,
    _build_loopback_score_command,
    _build_reference,
    _score_candidate,
    analyze_quality,
    measurement_configuration_fingerprint,
    search_bitrate_candidates,
)
from core.vmaf_runtime import (
    COARSE_VMAF_SUBSAMPLE,
    EXACT_VMAF_SUBSAMPLE,
    VMAF_STANDARD_MODEL,
    VmafEncodeMetadata,
)


def _capabilities(**overrides: object) -> AnalysisCapabilities:
    payload = dict(
        libvmaf=True,
        libvmaf_cuda=False,
        loopback_decoder=False,
        hwaccels=frozenset({"videotoolbox"}),
        filters=frozenset({"libvmaf", "scale_vt"}),
        encoders=frozenset({"libx265", "libsvtav1", "hevc_videotoolbox"}),
        scale_vt=True,
        scale_cuda=False,
        videotoolbox_hwaccel=True,
        cuda_hwaccel=False,
        videotoolbox_prio_speed=True,
    )
    payload.update(overrides)
    return AnalysisCapabilities(**payload)  # type: ignore[arg-type]


def _encoder(name: str, backend: BackendChoice, *, two_pass: bool = False, preset: str | None = None) -> EncoderInfo:
    return EncoderInfo(
        codec=CodecChoice.AV1 if "av1" in name or name == "libsvtav1" else CodecChoice.HEVC,
        backend=backend,
        encoder_name=name,
        supports_two_pass=two_pass,
        default_preset=preset,
    )


class AnalysisCapabilityParsingTestCase(unittest.TestCase):
    def test_filter_and_decoder_parsers(self) -> None:
        filters = parse_filter_names(
            "Filters:\n"
            " .. libvmaf           VV->V      Calculate the VMAF\n"
            " TSC scale_vt         V->V       Scale Videotoolbox frames\n"
            " .. libvmaf_cuda      VV->V      CUDA VMAF\n"
        )
        self.assertEqual(filters, frozenset({"libvmaf", "scale_vt", "libvmaf_cuda"}))
        decoders = parse_decoder_names(" V..... loopback            Loopback decoder\n V....D hevc                HEVC")
        self.assertIn("loopback", decoders)

    def test_loopback_help_detection_and_legacy_fallback(self) -> None:
        self.assertTrue(help_exposes_loopback_decoder("  -dec[:stream] decoder   loopback decoder"))
        self.assertFalse(help_exposes_loopback_decoder("  -decode                  unused"))
        plan = build_analysis_execution_plan(
            tier=AnalysisTier.EXACT,
            encoder_info=_encoder("libx265", BackendChoice.CPU, two_pass=True, preset="slow"),
            production_preset="slow",
            production_two_pass=True,
            capabilities=_capabilities(loopback_decoder=True),
            enable_loopback=True,
        )
        self.assertTrue(plan.use_loopback)
        fallback = legacy_loopback_plan(plan, reason="loopback failed")
        self.assertFalse(fallback.use_loopback)
        self.assertEqual(fallback.fallback_reason, "loopback failed")

    def test_capability_report_does_not_require_optional_features(self) -> None:
        report = format_analysis_capability_report(_capabilities(libvmaf_cuda=False, loopback_decoder=False))
        self.assertIn("CPU VMAF", report)
        self.assertIn("CUDA VMAF filter (not enabled for v1)", report)
        self.assertIn("Loopback decoder", report)
        self.assertIn("VideoToolbox", report)


class AnalysisExecutionPlanTestCase(unittest.TestCase):
    def test_coarse_uses_faster_preset_and_one_pass(self) -> None:
        coarse = build_analysis_execution_plan(
            tier=AnalysisTier.COARSE,
            encoder_info=_encoder("libx265", BackendChoice.CPU, two_pass=True, preset="slow"),
            production_preset="slow",
            production_two_pass=True,
            capabilities=_capabilities(),
        )
        exact = build_analysis_execution_plan(
            tier=AnalysisTier.EXACT,
            encoder_info=_encoder("libx265", BackendChoice.CPU, two_pass=True, preset="slow"),
            production_preset="slow",
            production_two_pass=True,
            capabilities=_capabilities(),
        )
        self.assertEqual(coarse.encoder_name, "libx265")
        self.assertEqual(coarse.encoder_preset, "fast")
        self.assertFalse(coarse.two_pass)
        self.assertEqual(coarse.vmaf_subsample, COARSE_VMAF_SUBSAMPLE)
        self.assertEqual(exact.encoder_preset, "slow")
        self.assertTrue(exact.two_pass)
        self.assertEqual(exact.vmaf_subsample, EXACT_VMAF_SUBSAMPLE)
        self.assertGreaterEqual(exact.vmaf_threads, 1)

    def test_svt_and_nvenc_coarse_presets_stay_on_the_same_encoder(self) -> None:
        self.assertEqual(coarse_encoder_preset("libsvtav1", "5"), "8")
        self.assertEqual(coarse_encoder_preset("hevc_nvenc", "p6"), "p4")
        svt = build_analysis_execution_plan(
            tier=AnalysisTier.COARSE,
            encoder_info=_encoder("libsvtav1", BackendChoice.CPU, preset="5"),
            production_preset="5",
            production_two_pass=False,
            capabilities=_capabilities(),
        )
        self.assertEqual(svt.encoder_name, "libsvtav1")
        self.assertEqual(svt.encoder_preset, "8")

    def test_videotoolbox_coarse_adds_prio_speed_exact_does_not(self) -> None:
        encoder = _encoder("hevc_videotoolbox", BackendChoice.VIDEOTOOLBOX)
        coarse = build_analysis_execution_plan(
            tier=AnalysisTier.COARSE,
            encoder_info=encoder,
            production_preset=None,
            production_two_pass=False,
            capabilities=_capabilities(),
        )
        exact = build_analysis_execution_plan(
            tier=AnalysisTier.EXACT,
            encoder_info=encoder,
            production_preset=None,
            production_two_pass=False,
            capabilities=_capabilities(),
        )
        self.assertEqual(coarse.encoder_extra_args, ("-prio_speed", "1"))
        self.assertEqual(exact.encoder_extra_args, ())
        self.assertEqual(coarse.source_decode_acceleration, SOURCE_DECODE_VIDEOTOOLBOX)
        item = EncodePlanItem(
            source_path=Path("in.mp4"),
            output_path=Path("out.mp4"),
            media_info=None,
            encoder_info=encoder,
            options=EncodeOptions(codec=CodecChoice.HEVC, backend=BackendChoice.VIDEOTOOLBOX),
            target_video_bitrate_bps=1_000_000,
        )
        exact_args = build_video_args(item, extra_args=exact.encoder_extra_args)
        coarse_args = build_video_args(item, extra_args=coarse.encoder_extra_args)
        self.assertEqual(exact_args[exact_args.index("-allow_sw") + 1], "0")
        self.assertNotIn("-prio_speed", exact_args)
        self.assertEqual(coarse_args[coarse_args.index("-allow_sw") + 1], "0")
        self.assertEqual(coarse_args[coarse_args.index("-prio_speed") + 1], "1")

    def test_software_policy_never_selects_videotoolbox_decode(self) -> None:
        plan = build_analysis_execution_plan(
            tier=AnalysisTier.EXACT,
            encoder_info=_encoder("hevc_videotoolbox", BackendChoice.VIDEOTOOLBOX),
            production_preset=None,
            production_two_pass=False,
            capabilities=_capabilities(),
            decode_policy=AnalysisDecodePolicy.SOFTWARE,
        )
        self.assertEqual(plan.source_decode_acceleration, SOURCE_DECODE_SOFTWARE)
        self.assertEqual(source_decode_args(SOURCE_DECODE_SOFTWARE), [])
        self.assertEqual(source_decode_args(SOURCE_DECODE_VIDEOTOOLBOX), ["-hwaccel", "videotoolbox"])

    def test_av1_never_binds_videotoolbox_encoder_but_may_decode_with_vt(self) -> None:
        with self.assertRaises(ValueError):
            build_analysis_execution_plan(
                tier=AnalysisTier.EXACT,
                encoder_info=_encoder("av1_videotoolbox", BackendChoice.VIDEOTOOLBOX),
                production_preset="5",
                production_two_pass=False,
                capabilities=_capabilities(),
            )
        plan = build_analysis_execution_plan(
            tier=AnalysisTier.COARSE,
            encoder_info=_encoder("libsvtav1", BackendChoice.CPU, preset="5"),
            production_preset="5",
            production_two_pass=False,
            capabilities=_capabilities(),
        )
        self.assertEqual(plan.encoder_name, "libsvtav1")
        self.assertEqual(plan.source_decode_acceleration, SOURCE_DECODE_VIDEOTOOLBOX)

    def test_static_cuda_presence_does_not_enable_v1_cuda(self) -> None:
        cuda = build_analysis_execution_plan(
            tier=AnalysisTier.EXACT,
            encoder_info=_encoder("hevc_nvenc", BackendChoice.NVENC, preset="p6"),
            production_preset="p6",
            production_two_pass=False,
            capabilities=_capabilities(
                libvmaf_cuda=True,
                scale_cuda=True,
                cuda_hwaccel=True,
                hwaccels=frozenset({"cuda"}),
                videotoolbox_hwaccel=False,
            ),
        )
        self.assertEqual(cuda.vmaf_backend, VmafBackend.CPU)
        fallback = cpu_vmaf_plan(cuda, reason="cuda vmaf failed")
        self.assertEqual(fallback.vmaf_backend, VmafBackend.CPU)
        self.assertFalse(fallback.use_loopback)

    def test_source_decode_fallback_drops_hardware(self) -> None:
        plan = build_analysis_execution_plan(
            tier=AnalysisTier.EXACT,
            encoder_info=_encoder("hevc_videotoolbox", BackendChoice.VIDEOTOOLBOX),
            production_preset=None,
            production_two_pass=False,
            capabilities=_capabilities(),
        )
        fallback = software_source_plan(plan, reason="vt failed")
        self.assertEqual(fallback.source_decode_acceleration, SOURCE_DECODE_SOFTWARE)
        self.assertEqual(fallback.encoder_name, "hevc_videotoolbox")


class SmartAnalyseV2TestCase(unittest.TestCase):
    def test_ffv1_reference_never_gets_videotoolbox_by_default_helper_can_add_it(self) -> None:
        item = EncodePlanItem(
            source_path=Path("movie.mp4"),
            output_path=Path("out.mp4"),
            media_info=None,
            encoder_info=_encoder("hevc_videotoolbox", BackendChoice.VIDEOTOOLBOX),
            options=EncodeOptions(),
        )
        software = _build_reference(Path("ffmpeg"), item, type("W", (), {"start_sec": 1.0, "duration_sec": 5.0})(), Path("ref.mkv"))
        hardware = _build_reference(
            Path("ffmpeg"),
            item,
            type("W", (), {"start_sec": 1.0, "duration_sec": 5.0})(),
            Path("ref.mkv"),
            decode_acceleration=SOURCE_DECODE_VIDEOTOOLBOX,
        )
        self.assertNotIn("-hwaccel", software)
        self.assertEqual(hardware[hardware.index("-hwaccel") + 1], "videotoolbox")
        self.assertLess(hardware.index("-hwaccel"), hardware.index("-i"))
        self.assertIn("ffv1", software)
        self.assertIn("settb=AVTB,setpts=PTS-STARTPTS", software)

    def test_loopback_command_uses_dec_and_keeps_legacy_builder(self) -> None:
        encoder = _encoder("hevc_videotoolbox", BackendChoice.VIDEOTOOLBOX)
        item = EncodePlanItem(
            source_path=Path("movie.mp4"),
            output_path=Path("out.mp4"),
            media_info=None,
            encoder_info=encoder,
            options=EncodeOptions(),
            target_video_bitrate_bps=1_000_000,
        )
        plan = build_analysis_execution_plan(
            tier=AnalysisTier.COARSE,
            encoder_info=encoder,
            production_preset=None,
            production_two_pass=False,
            capabilities=_capabilities(loopback_decoder=True),
            enable_loopback=True,
        )
        command = _build_loopback_score_command(
            Path("ffmpeg"),
            item,
            type("W", (), {"start_sec": 0.0, "duration_sec": 5.0})(),
            Path("cand.mkv"),
            plan,
            model_spec=VMAF_STANDARD_MODEL,
            encode_metadata=VmafEncodeMetadata(1920, 1080, 8),
            log_name="vmaf.json",
        )
        self.assertIn("-dec:v", command)
        self.assertIn("hevc", command)
        self.assertIn("[dec:v]", command[command.index("-filter_complex") + 1])

    def test_selected_bitrate_cannot_come_from_coarse_candidates(self) -> None:
        coarse_only: list[int] = []
        exact_only: list[int] = []

        def coarse(bitrate: int) -> QualityCandidateResult:
            coarse_only.append(bitrate)
            return QualityCandidateResult(video_bitrate_bps=bitrate, min_vmaf=96.0, predicted_output_bytes=100)

        def exact(bitrate: int) -> QualityCandidateResult:
            exact_only.append(bitrate)
            return QualityCandidateResult(video_bitrate_bps=bitrate, min_vmaf=96.0, predicted_output_bytes=100)

        coarse_candidates, coarse_selected, _ = search_bitrate_candidates(
            evaluate=coarse,
            min_bitrate_bps=500_000,
            budget_bitrate_bps=2_000_000,
            required_search_ceiling_bps=3_000_000,
            min_vmaf=95.0,
            max_candidates=COARSE_MAX_CANDIDATES,
            max_output_bytes=1_000,
            tolerance_bps=search_tolerance_bps(3_000_000),
        )
        exact_candidates, exact_selected, _ = search_bitrate_candidates(
            evaluate=exact,
            min_bitrate_bps=500_000,
            budget_bitrate_bps=coarse_selected or 2_000_000,
            required_search_ceiling_bps=3_000_000,
            min_vmaf=95.0,
            max_candidates=EXACT_MAX_CANDIDATES,
            max_output_bytes=1_000,
            tolerance_bps=search_tolerance_bps(3_000_000),
        )
        self.assertTrue(coarse_candidates)
        self.assertTrue(exact_candidates)
        self.assertIsNotNone(exact_selected)
        self.assertIn(exact_selected, exact_only)
        self.assertLessEqual(len(coarse_only), 4)
        self.assertLessEqual(len(exact_only), 3)

    def test_old_receipt_schema_is_ignored_and_backend_changes_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = AnalysisReceipt(
                schema_version=1,
                measurement_fingerprint="a" * 64,
                source_identity={"path": "s"},
                ffmpeg_identity={"path": "f"},
                encoder_identity={"encoder": "libx265"},
                sample_scheme_version=1,
                sample_windows=[(0.0, 10.0)],
                candidates=[QualityCandidateResult(video_bitrate_bps=1_000_000, min_vmaf=96.0)],
            )
            path = root / "analysis" / "receipts" / f"{'a' * 64}.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "measurement_fingerprint": old.measurement_fingerprint,
                        "source_identity": old.source_identity,
                        "ffmpeg_identity": old.ffmpeg_identity,
                        "encoder_identity": old.encoder_identity,
                        "sample_scheme_version": 1,
                        "sample_windows": old.sample_windows,
                        "candidates": [],
                        "created_at": "",
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(load_analysis_receipt(root, "a" * 64))

            source = root / "source.mov"
            source.write_bytes(b"source")
            ffmpeg = root / "ffmpeg"
            ffmpeg.write_bytes(b"binary")
            item = EncodePlanItem(
                source_path=source,
                output_path=root / "out.mp4",
                media_info=MediaInfo(
                    path=source,
                    duration=60.0,
                    format_bitrate_bps=4_000_000,
                    video_bitrate_bps=3_000_000,
                    audio_bitrate_bps=128_000,
                    width=1920,
                    height=1080,
                    fps=30.0,
                    video_codec="h264",
                    audio_codec="aac",
                    pix_fmt="yuv420p",
                    bit_depth=8,
                ),
                encoder_info=_encoder("libx265", BackendChoice.CPU, two_pass=True, preset="slow"),
                options=EncodeOptions(encoder_preset="slow"),
            )
            cpu = measurement_configuration_fingerprint(ffmpeg, item, vmaf_backend=VmafBackend.CPU)
            cuda = measurement_configuration_fingerprint(ffmpeg, item, vmaf_backend=VmafBackend.CUDA)
            self.assertNotEqual(cpu, cuda)
            self.assertEqual(SMART_SAMPLE_SCHEME_VERSION, 3)
            self.assertEqual(SMART_ANALYSIS_ALGORITHM_VERSION, 4)
            self.assertEqual(ANALYSIS_RECEIPT_SCHEMA_VERSION, 3)
            from core.smart_quality import measurement_configuration_payload

            payload = measurement_configuration_payload(ffmpeg, item, vmaf_backend=VmafBackend.CPU)
            self.assertEqual(payload["sample_scheme_version"], 3)
            self.assertEqual(payload["vmaf_subsample"], 1)
            self.assertEqual(payload["vmaf_backend"], "cpu")
            self.assertEqual(payload["analysis_algorithm_version"], 4)
            self.assertEqual(payload["vmaf_resolution_mode"], "display_model_canvas")
            self.assertEqual(payload["vmaf_generation"], "v1")
            self.assertEqual(payload["vmaf_model"], "vmaf_v1.0.16_3d0h")
            self.assertEqual(payload["vmaf_measurement_pix_fmt"], "yuv420p10le")
            self.assertEqual(payload["vmaf_measurement_bit_depth"], 10)
            self.assertEqual(payload["vmaf_scale_algorithm"], "bicubic")
            self.assertEqual(payload["vmaf_aspect_policy"], "fit_and_pad")
            self.assertEqual(payload["candidate_encode_bit_depth"], 8)
            self.assertEqual(payload["vmaf_pooling"], "lowest_sampled_window_mean")
            self.assertNotIn("n_threads", payload)
            self.assertNotIn("vmaf_threads", payload)
            assert item.media_info is not None
            item.media_info.fps = 60.0
            self.assertNotEqual(
                measurement_configuration_fingerprint(ffmpeg, item, vmaf_backend=VmafBackend.CPU),
                cpu,
            )
            item.media_info.fps = 30.0
            item.options.pix_fmt = "p010le"
            self.assertNotEqual(
                measurement_configuration_fingerprint(ffmpeg, item, vmaf_backend=VmafBackend.CPU),
                cpu,
            )
            item.options.pix_fmt = "yuv420p"
            item.media_info.bit_depth = 10
            self.assertNotEqual(
                measurement_configuration_fingerprint(ffmpeg, item, vmaf_backend=VmafBackend.CPU),
                cpu,
            )
            item.media_info.bit_depth = 8
            with patch("core.smart_quality.SMART_SAMPLE_SCHEME_VERSION", 99):
                self.assertNotEqual(
                    measurement_configuration_fingerprint(ffmpeg, item, vmaf_backend=VmafBackend.CPU),
                    cpu,
                )

    def test_analyze_quality_persists_only_exact_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mov"
            source.write_bytes(b"x" * 100_000_000)
            ffmpeg = root / "ffmpeg"
            ffmpeg.write_bytes(b"binary")
            item = EncodePlanItem(
                source_path=source,
                output_path=root / "out.mp4",
                media_info=MediaInfo(
                    path=source,
                    duration=60.0,
                    format_bitrate_bps=4_000_000,
                    video_bitrate_bps=3_000_000,
                    audio_bitrate_bps=128_000,
                    width=1920,
                    height=1080,
                    fps=30.0,
                    video_codec="h264",
                    audio_codec="aac",
                    audio_stream_count=1,
                    pix_fmt="yuv420p",
                    color_transfer="bt709",
                ),
                encoder_info=_encoder("libx265", BackendChoice.CPU, two_pass=True, preset="slow"),
                options=EncodeOptions(encoder_preset="slow"),
            )
            seen_tiers: list[str] = []

            def score(*_args, **kwargs):
                plan = kwargs.get("plan")
                bitrate = _args[3]
                if plan is not None:
                    seen_tiers.append(plan.tier.value)
                    if plan.tier is AnalysisTier.COARSE:
                        return QualityCandidateResult(
                            video_bitrate_bps=bitrate,
                            min_vmaf=96.0 if bitrate >= 800_000 else 90.0,
                            segment_vmaf=[96.0, 96.0, 96.0] if bitrate >= 800_000 else [90.0],
                            observed_video_bitrate_bps=bitrate,
                            predicted_output_bytes=bitrate // 10,
                        )
                return QualityCandidateResult(
                    video_bitrate_bps=bitrate,
                    min_vmaf=95.5 if bitrate >= 900_000 else 91.0,
                    segment_vmaf=[96.0, 95.5, 96.2] if bitrate >= 900_000 else [91.0, 91.0, 91.0],
                    observed_video_bitrate_bps=bitrate,
                    predicted_output_bytes=bitrate // 10,
                )

            with (
                patch(
                    "core.smart_quality.select_vmaf_runtime",
                    return_value=VmafRuntimeSupport(
                        VmafBackend.CPU, "vmaf_v1.0.16_3d0h", True
                    ),
                ),
                patch("core.smart_quality.detect_analysis_capabilities", return_value=_capabilities()),
                patch("core.smart_quality._run_logged"),
                patch("core.smart_quality._score_candidate", side_effect=score),
            ):
                result = analyze_quality(ffmpeg, item, root, root / "log.txt")

            self.assertIn("coarse", seen_tiers)
            self.assertIn("exact", seen_tiers)
            self.assertEqual(result.status, QualitySearchStatus.FOUND)
            receipt = load_analysis_receipt(root, result.measurement_fingerprint)
            self.assertIsNotNone(receipt)
            assert receipt is not None
            self.assertTrue(receipt.candidates)
            self.assertTrue(all(len(candidate.segment_vmaf) == 3 for candidate in receipt.candidates))
            self.assertIn(result.selected_video_bitrate_bps, [candidate.video_bitrate_bps for candidate in result.candidates])
            selected = next(
                candidate
                for candidate in result.candidates
                if candidate.video_bitrate_bps == result.selected_video_bitrate_bps
            )
            self.assertGreaterEqual(selected.min_vmaf, item.options.min_vmaf)

    def test_loopback_failure_falls_back_to_legacy_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mov"
            source.write_bytes(b"x" * 100_000_000)
            ffmpeg = root / "ffmpeg"
            ffmpeg.write_bytes(b"binary")
            item = EncodePlanItem(
                source_path=source,
                output_path=root / "out.mp4",
                media_info=MediaInfo(
                    path=source,
                    duration=12.0,
                    format_bitrate_bps=4_000_000,
                    video_bitrate_bps=3_000_000,
                    audio_bitrate_bps=128_000,
                    width=1920,
                    height=1080,
                    fps=29.97,
                    video_codec="h264",
                    audio_codec="aac",
                    audio_stream_count=1,
                    pix_fmt="yuv420p",
                    color_transfer="bt709",
                ),
                encoder_info=_encoder("libx265", BackendChoice.CPU, two_pass=True, preset="slow"),
                options=EncodeOptions(encoder_preset="slow"),
            )
            calls: list[str] = []

            def fake_loopback(*_args, **_kwargs):
                calls.append("loopback")
                raise RuntimeError("loopback encoder failed")

            def fake_score(*_args, **kwargs):
                calls.append("legacy")
                bitrate = _args[3]
                return QualityCandidateResult(
                    video_bitrate_bps=bitrate,
                    min_vmaf=96.0,
                    segment_vmaf=[96.0, 96.0, 96.0],
                    observed_video_bitrate_bps=bitrate,
                )

            loopback_caps = _capabilities(loopback_decoder=True)
            with (
                patch(
                    "core.smart_quality.select_vmaf_runtime",
                    return_value=VmafRuntimeSupport(
                        VmafBackend.CPU, "vmaf_v1.0.16_3d0h", True
                    ),
                ),
                patch("core.smart_quality.detect_analysis_capabilities", return_value=loopback_caps),
                patch("core.smart_quality.build_analysis_execution_plan") as planner,
                patch("core.smart_quality._run_logged"),
                patch("core.smart_quality._score_candidate_loopback", side_effect=fake_loopback),
                patch("core.smart_quality._score_candidate", side_effect=fake_score),
            ):
                def plan_for(*, tier, **kwargs):
                    return build_analysis_execution_plan(
                        tier=tier,
                        encoder_info=item.encoder_info,
                        production_preset="slow",
                        production_two_pass=True,
                        capabilities=loopback_caps,
                        enable_loopback=True,
                    )

                planner.side_effect = plan_for
                result = analyze_quality(ffmpeg, item, root, root / "log.txt")

            self.assertIn("loopback", calls)
            self.assertIn("legacy", calls)
            self.assertEqual(result.status, QualitySearchStatus.FOUND)

    def test_videotoolbox_reference_failure_retries_software(self) -> None:
        item = EncodePlanItem(
            source_path=Path("movie.mp4"),
            output_path=Path("out.mp4"),
            media_info=None,
            encoder_info=_encoder("hevc_videotoolbox", BackendChoice.VIDEOTOOLBOX),
            options=EncodeOptions(),
        )
        from core.smart_quality import SampleWindow, SmartCommandError

        command = _build_reference(
            Path("ffmpeg"),
            item,
            SampleWindow(0.0, 5.0),
            Path("ref.mkv"),
            decode_acceleration=SOURCE_DECODE_VIDEOTOOLBOX,
        )
        self.assertEqual(command[command.index("-hwaccel") + 1], "videotoolbox")
        fallback = software_source_plan(
            build_analysis_execution_plan(
                tier=AnalysisTier.EXACT,
                encoder_info=item.encoder_info,
                production_preset=None,
                production_two_pass=False,
                capabilities=_capabilities(),
            ),
            reason="VideoToolbox operation failed",
        )
        self.assertEqual(fallback.source_decode_acceleration, SOURCE_DECODE_SOFTWARE)
        self.assertIsInstance(SmartCommandError(1, ["ffmpeg"], "reference extraction", "vt fail"), SmartCommandError)

    def test_concurrency_limit_stays_resource_bounded(self) -> None:
        from core.analysis_concurrency import analysis_concurrency_limit

        self.assertEqual(analysis_concurrency_limit(cpu_count=4), 1)
        self.assertEqual(analysis_concurrency_limit(cpu_count=8), 2)
        self.assertLessEqual(SMART_ANALYSIS_SEMAPHORE._value, 2)
        self.assertGreaterEqual(SMART_ANALYSIS_SEMAPHORE._value, 1)

    def test_score_candidate_early_rejects_remaining_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mov"
            source.write_bytes(b"s" * 100_000)
            item = EncodePlanItem(
                source_path=source,
                output_path=root / "out.mp4",
                media_info=None,
                encoder_info=_encoder("libx265", BackendChoice.CPU, two_pass=True, preset="slow"),
                options=EncodeOptions(encoder_preset="slow", min_vmaf=95.0),
            )
            from core.models import MediaInfo

            item.media_info = MediaInfo(
                path=source,
                duration=60.0,
                format_bitrate_bps=4_000_000,
                video_bitrate_bps=3_000_000,
                audio_bitrate_bps=128_000,
                width=1920,
                height=1080,
                fps=30.0,
                video_codec="h264",
                audio_codec="aac",
                audio_stream_count=1,
            )
            references = [root / "r0.mkv", root / "r1.mkv", root / "r2.mkv"]
            for path in references:
                path.write_bytes(b"ref")
            scored: list[int] = []

            def fake_run(command, _log, **kwargs):
                phase = kwargs.get("phase")
                if phase == "candidate encode":
                    Path(command[-1]).write_bytes(b"c" * 1_000)
                elif phase == "VMAF scoring":
                    scored.append(1)
                    graph = command[command.index("-filter_complex") + 1]
                    name = graph.split("log_path='")[1].split("'")[0]
                    (kwargs["cwd"] / name).write_text(
                        json.dumps({"pooled_metrics": {"vmaf": {"mean": 90.0}}}),
                        encoding="utf-8",
                    )

            log_path = root / "smart.log"
            with log_path.open("w", encoding="utf-8") as log_file, patch(
                "core.smart_quality._run_logged", side_effect=fake_run
            ):
                result = _score_candidate(
                    Path("ffmpeg"),
                    item,
                    references,
                    900_000,
                    root,
                    root,
                    log_file,
                    cancel_check=None,
                    process_callback=None,
                    window_durations_sec=[5.0, 5.0, 5.0],
                    min_vmaf_target=95.0,
                    window_order=[1, 0, 2],
                )
            self.assertEqual(len(scored), 1)
            self.assertEqual(result.segment_vmaf, [90.0])
            self.assertLess(result.min_vmaf, 95.0)


if __name__ == "__main__":
    unittest.main()
