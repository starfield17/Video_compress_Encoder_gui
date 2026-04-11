from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable

from core.bitrate_policy import choose_ratio, compute_target_video_bitrate
from core.discover_ffmpeg import discover_ffmpeg_tools
from core.encoder_caps import is_valid_preset, list_available_encoders, preset_choices_for_encoder, resolve_encoder
from core.models import (
    EncodeOptions,
    EncodePlan,
    EncodePlanItem,
    OperationCancelledError,
    VideoFileItem,
)
from core.external_subtitles import discover_external_subtitles
from core.path_utils import build_output_path, choose_output_root
from core.probe_media import probe_media_info
from core.safety_checks import validate_plan_item, validate_workdir
from core.scan_videos import collect_video_files


def _emit(progress_callback: Callable[[str], None] | None, message: str) -> None:
    if progress_callback is not None:
        progress_callback(message)


def _emit_progress(
    event_callback: Callable[[dict[str, object]], None] | None,
    **event: object,
) -> None:
    if event_callback is not None:
        event_callback(event)


def _iter_sources(
    input_path: Path | None,
    recursive: bool,
    files: Iterable[VideoFileItem] | None,
) -> tuple[Path, list[VideoFileItem]]:
    if files is not None:
        file_items = list(files)
        if not file_items:
            raise ValueError("No video files were provided for planning.")
        root = file_items[0].path.parent
        return root, file_items
    if input_path is None:
        raise ValueError("input_path or files must be provided.")
    input_root = input_path.expanduser().resolve()
    return input_root, collect_video_files(input_root, recursive)


def build_encode_plan(
    input_path: Path | None,
    options: EncodeOptions,
    *,
    output_dir: Path | None = None,
    workdir: Path = Path("workdir"),
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    files: Iterable[VideoFileItem] | None = None,
    progress_callback: Callable[[str], None] | None = None,
    progress_event_callback: Callable[[dict[str, object]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> EncodePlan:
    _emit(progress_callback, "Planning started.")
    _emit_progress(progress_event_callback, stage="planning", state="started", percent=0.0)
    input_root, file_items = _iter_sources(input_path, options.recursive, files)
    if not file_items:
        raise FileNotFoundError("No processable video files were found.")

    workdir = validate_workdir(workdir)
    _emit(progress_callback, f"Validated workdir: {workdir}")
    ffmpeg, ffprobe = discover_ffmpeg_tools(ffmpeg_path, ffprobe_path)
    _emit(progress_callback, f"Using ffmpeg: {ffmpeg}")
    _emit(progress_callback, f"Using ffprobe: {ffprobe}")
    available_encoders = list_available_encoders(ffmpeg)
    _emit(progress_callback, f"Detected {len(available_encoders)} available encoders.")
    encoder_info = resolve_encoder(options.codec, options.backend, available_encoders, ffmpeg)
    _emit(
        progress_callback,
        f"Resolved encoder: {encoder_info.encoder_name} ({encoder_info.backend.value})",
    )
    if options.encoder_preset is None and not options.parallel_enabled:
        default_preset = encoder_info.default_preset
        if default_preset:
            choices = preset_choices_for_encoder(ffmpeg, encoder_info.encoder_name)
            if choices and not is_valid_preset(ffmpeg, encoder_info.encoder_name, default_preset):
                _emit(
                    progress_callback,
                    f"Default encoder preset {default_preset!r} is not valid for {encoder_info.encoder_name}; falling back to encoder defaults.",
                )
                default_preset = None
        options = replace(options, encoder_preset=default_preset)
        if default_preset:
            _emit(progress_callback, f"Using default encoder preset: {default_preset}")

    output_root = choose_output_root(input_root, output_dir, options.codec)
    _emit(progress_callback, f"Output root: {output_root}")
    items: list[EncodePlanItem] = []
    ratio = choose_ratio(options.codec, options.ratio)
    _emit(progress_callback, f"Effective bitrate ratio: {ratio:.3f}")
    _emit(progress_callback, f"Discovered {len(file_items)} input item(s).")

    for index, file_item in enumerate(file_items, start=1):
        if cancel_check is not None and cancel_check():
            _emit(progress_callback, "Planning cancelled by user.")
            _emit_progress(
                progress_event_callback,
                stage="planning",
                state="cancelled",
                percent=((index - 1) / max(len(file_items), 1)) * 100.0,
            )
            raise OperationCancelledError("Planning cancelled.")
        _emit(
            progress_callback,
            f"[{index}/{len(file_items)}] Probing source: {file_item.path}",
        )
        _emit_progress(
            progress_event_callback,
            stage="planning",
            state="probing",
            file_path=str(file_item.path),
            file_name=file_item.path.name,
            current=index,
            total=len(file_items),
            percent=((index - 1) / max(len(file_items), 1)) * 100.0,
        )
        default_output = build_output_path(
            source_path=file_item.path,
            input_root=input_root if input_root.is_dir() else file_item.path.parent,
            output_root=output_root,
            codec=options.codec,
            container=options.container,
        )
        try:
            media_info = probe_media_info(ffprobe, file_item.path)
            target_bitrate = compute_target_video_bitrate(
                media_info.video_bitrate_bps,
                ratio,
                options.min_video_kbps,
                options.max_video_kbps,
            )
            item = EncodePlanItem(
                source_path=file_item.path,
                output_path=default_output,
                media_info=media_info,
                encoder_info=encoder_info,
                options=options,
                target_video_bitrate_bps=target_bitrate,
            )
            if options.copy_external_subtitles:
                sidecars = discover_external_subtitles(file_item.path)
                if sidecars:
                    item.warnings.append(
                        f"Will copy {len(sidecars)} external subtitle file(s) next to the output."
                    )
            validate_plan_item(
                source_path=file_item.path,
                output_path=default_output,
                options=options,
                encoder_info=encoder_info,
                workdir=workdir,
            )
            _emit(
                progress_callback,
                f"[{index}/{len(file_items)}] Planned: {file_item.path.name} -> {default_output}",
            )
            _emit_progress(
                progress_event_callback,
                stage="planning",
                state="planned",
                file_path=str(file_item.path),
                file_name=file_item.path.name,
                current=index,
                total=len(file_items),
                percent=(index / max(len(file_items), 1)) * 100.0,
                output_path=str(default_output),
            )
        except Exception as exc:
            item = EncodePlanItem(
                source_path=file_item.path,
                output_path=default_output,
                media_info=None,
                encoder_info=encoder_info,
                options=options,
                target_video_bitrate_bps=0,
                skip_reason=str(exc),
            )
            _emit(
                progress_callback,
                f"[{index}/{len(file_items)}] Skipped: {file_item.path.name} | {exc}",
            )
            _emit_progress(
                progress_event_callback,
                stage="planning",
                state="skipped",
                file_path=str(file_item.path),
                file_name=file_item.path.name,
                current=index,
                total=len(file_items),
                percent=(index / max(len(file_items), 1)) * 100.0,
                error=str(exc),
            )
        items.append(item)

    _emit(progress_callback, "Planning finished.")
    _emit_progress(progress_event_callback, stage="planning", state="finished", percent=100.0)
    return EncodePlan(
        items=items,
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        input_root=input_root,
        output_root=output_root,
    )
