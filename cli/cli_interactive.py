from __future__ import annotations

from core.bitrate_policy import human_kbps
from core.i18n import Translator
from core.models import EncodePlan, EncodeResult, PreviewResult


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
        fps = f"{media.fps:.3f}" if media and media.fps else "n/a"
        wh = f"{media.width}x{media.height}" if media and media.width and media.height else "n/a"
        print(f"[{tr.t('cli.plan_ready')}] {item.source_path}")
        print(f"  {tr.t('cli.resolution')}: {wh}")
        print(f"  {tr.t('cli.fps')}: {fps}")
        print(f"  {tr.t('cli.source_bitrate')}: {human_kbps(media.video_bitrate_bps)}")
        print(f"  {tr.t('cli.target_bitrate')}: {human_kbps(item.target_video_bitrate_bps)}")
        print(f"  {tr.t('cli.encoder')}: {encoder.encoder_name} ({encoder.backend.value})")
        print(f"  {tr.t('cli.output')}: {item.output_path}")
    print(f"{tr.t('cli.ffmpeg')}: {plan.ffmpeg_path}")
    print(f"{tr.t('cli.ffprobe')}: {plan.ffprobe_path}")
    print(f"{tr.t('cli.output_root')}: {plan.output_root}")


def print_encode_results(results: list[EncodeResult], tr: Translator) -> None:
    for result in results:
        if result.skipped:
            print(f"[{tr.t('cli.result_skipped')}] {result.source_path}")
            print(f"  {tr.t('cli.reason')}: {result.error_message}")
            continue
        if result.success:
            print(f"[{tr.t('cli.result_success')}] {result.source_path} -> {result.output_path}")
        else:
            print(f"[{tr.t('cli.result_failed')}] {result.source_path}")
            print(f"  {tr.t('cli.reason')}: {result.error_message}")
        if result.log_path:
            print(f"  {tr.t('cli.log_path')}: {result.log_path}")


def print_preview_result(result: PreviewResult, tr: Translator) -> None:
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

