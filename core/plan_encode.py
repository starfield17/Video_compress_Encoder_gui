from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable

from core.bitrate_policy import choose_ratio, compute_target_video_bitrate
from core.discover_ffmpeg import discover_ffmpeg_tools
from core.encoder_caps import list_available_encoders, resolve_encoder
from core.models import EncodeOptions, EncodePlan, EncodePlanItem, VideoFileItem
from core.path_utils import build_output_path, choose_output_root
from core.probe_media import probe_media_info
from core.safety_checks import validate_plan_item, validate_workdir
from core.scan_videos import collect_video_files


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
) -> EncodePlan:
    input_root, file_items = _iter_sources(input_path, options.recursive, files)
    if not file_items:
        raise FileNotFoundError("No processable video files were found.")

    workdir = validate_workdir(workdir)
    ffmpeg, ffprobe = discover_ffmpeg_tools(ffmpeg_path, ffprobe_path)
    available_encoders = list_available_encoders(ffmpeg)
    encoder_info = resolve_encoder(options.codec, options.backend, available_encoders)
    if options.encoder_preset is None:
        options = replace(options, encoder_preset=encoder_info.default_preset)

    output_root = choose_output_root(input_root, output_dir, options.codec)
    items: list[EncodePlanItem] = []
    ratio = choose_ratio(options.codec, options.ratio)

    for file_item in file_items:
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
            validate_plan_item(
                source_path=file_item.path,
                output_path=default_output,
                options=options,
                encoder_info=encoder_info,
                workdir=workdir,
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
        items.append(item)

    return EncodePlan(
        items=items,
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        input_root=input_root,
        output_root=output_root,
    )
