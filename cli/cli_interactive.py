from __future__ import annotations

from core.bitrate_policy import human_kbps
from core.i18n import Translator
from core.models import CompressionMode, EncodePlan, EncodeResult, PreviewResult, SmartPreviewResult
from core.smart_quality import build_decision_options


def _human_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{size_bytes} B"


def print_plan(plan: EncodePlan, tr: Translator) -> None:
    print(tr.t("cli.plan_header", count=len(plan.items)))
    for item in plan.items:
        if item.skip_reason:
            print(f"[{tr.t('cli.plan_skip')}] {item.source_path}")
            print(f"  {tr.t('cli.reason')}: {item.skip_reason}")
            print(f"  {tr.t('cli.output')}: {item.output_path}")
            continue

        media = item.media_info
        encoder = item.encoder_info
        if media is None or encoder is None:
            raise ValueError(f"Planned item is missing media or encoder information: {item.source_path}")
        fps = f"{media.fps:.3f}" if media and media.fps else "n/a"
        wh = f"{media.width}x{media.height}" if media and media.width and media.height else "n/a"
        print(f"[{tr.t('cli.plan_ready')}] {item.source_path}")
        print(f"  {tr.t('cli.resolution')}: {wh}")
        print(f"  {tr.t('cli.fps')}: {fps}")
        print(f"  {tr.t('cli.source_bitrate')}: {human_kbps(media.video_bitrate_bps)}")
        target = (
            tr.t("cli.pending_analysis")
            if item.options.compression_mode == CompressionMode.SMART and item.target_video_bitrate_bps <= 0
            else human_kbps(item.target_video_bitrate_bps)
        )
        print(f"  {tr.t('cli.target_bitrate')}: {target}")
        print(f"  {tr.t('cli.encoder')}: {encoder.encoder_name} ({encoder.backend.value})")
        print(f"  {tr.t('cli.output')}: {item.output_path}")
        for warning in item.warnings:
            print(f"  {tr.t('cli.note')}: {warning}")
    print(f"{tr.t('cli.ffmpeg')}: {plan.ffmpeg_path}")
    print(f"{tr.t('cli.ffprobe')}: {plan.ffprobe_path}")
    print(f"{tr.t('cli.output_root')}: {plan.output_root}")


def print_encode_results(results: list[EncodeResult], tr: Translator) -> None:
    for result in results:
        if result.needs_decision:
            print(f"[{tr.t('cli.result_needs_decision')}] {result.source_path}")
            print(f"  {tr.t('cli.reason')}: {result.error_message}")
            if result.rejected_output_path is not None:
                print(f"  {tr.t('cli.rejected_output')}: {result.rejected_output_path}")
            elif result.quality_search_result is not None:
                for option in build_decision_options(result.quality_search_result):
                    suffix = f"={option.suggested_value}" if option.suggested_value is not None else ""
                    print(f"  {tr.t('cli.available_decision')}: {option.action_code.value}{suffix}")
        elif result.skipped:
            print(f"[{tr.t('cli.result_skipped')}] {result.source_path}")
            print(f"  {tr.t('cli.reason')}: {result.error_message}")
        elif result.success:
            print(f"[{tr.t('cli.result_success')}] {result.source_path} -> {result.output_path}")
        else:
            print(f"[{tr.t('cli.result_failed')}] {result.source_path}")
            print(f"  {tr.t('cli.reason')}: {result.error_message}")
        quality = result.quality_search_result
        if quality is not None:
            if quality.min_vmaf is not None:
                print(f"  {tr.t('cli.minimum_vmaf')}: {quality.min_vmaf:.2f}")
            if quality.predicted_output_ratio is not None:
                print(f"  {tr.t('cli.predicted_ratio')}: {quality.predicted_output_ratio:.3f}")
            if quality.required_output_ratio is not None:
                print(f"  {tr.t('cli.required_ratio')}: {quality.required_output_ratio:.3f}")
        for copied_path in result.copied_external_subtitle_paths:
            print(f"  {tr.t('cli.external_subtitle_copied')}: {copied_path}")
        for warning in result.external_subtitle_warnings:
            print(f"  {tr.t('cli.external_subtitle_warning')}: {warning}")
        if result.log_path:
            print(f"  {tr.t('cli.log_path')}: {result.log_path}")


def print_preview_result(result: PreviewResult | SmartPreviewResult, tr: Translator) -> None:
    if isinstance(result, SmartPreviewResult):
        quality = result.quality_search_result
        label = tr.t("cli.preview_success") if result.success else tr.t("cli.result_failed")
        print(f"[{label}] {result.source_path}")
        if quality.selected_video_bitrate_bps:
            print(f"  {tr.t('cli.target_bitrate')}: {human_kbps(quality.selected_video_bitrate_bps)}")
        if quality.min_vmaf is not None:
            print(f"  {tr.t('cli.minimum_vmaf')}: {quality.min_vmaf:.2f}")
        if quality.predicted_output_ratio is not None:
            print(f"  {tr.t('cli.predicted_ratio')}: {quality.predicted_output_ratio:.3f}")
        if quality.required_output_ratio is not None:
            print(f"  {tr.t('cli.required_ratio')}: {quality.required_output_ratio:.3f}")
        if result.error_message:
            print(f"  {tr.t('cli.reason')}: {result.error_message}")
        if result.log_path:
            print(f"  {tr.t('cli.log_path')}: {result.log_path}")
        return

    if not result.success:
        print(f"[{tr.t('cli.result_failed')}] {result.job.source_path}")
        print(f"  {tr.t('cli.reason')}: {result.error_message}")
        if result.log_path:
            print(f"  {tr.t('cli.log_path')}: {result.log_path}")
        return

    print(f"[{tr.t('cli.preview_success')}] {result.job.source_path}")
    print(f"  {tr.t('cli.sample_source')}: {result.job.source_sample_path}")
    print(f"  {tr.t('cli.sample_encoded')}: {result.job.encoded_sample_path}")
    print(f"  {tr.t('cli.sample_source_size')}: {_human_size(result.source_sample_size)}")
    print(f"  {tr.t('cli.sample_encoded_size')}: {_human_size(result.encoded_sample_size)}")
    print(f"  {tr.t('cli.sample_ratio')}: {result.sample_compression_ratio:.3f}")
    print(f"  {tr.t('cli.estimated_output')}: {_human_size(result.estimated_full_output_size)}")
    for note in result.notes:
        print(f"  {tr.t('cli.note')}: {note}")
    if result.log_path:
        print(f"  {tr.t('cli.log_path')}: {result.log_path}")
