from __future__ import annotations

import os
from pathlib import Path

from core.models import AudioMode, ContainerChoice, EncodePlanItem, PreviewJob
from core.path_utils import passlog_prefix


def _null_sink() -> str:
    return "NUL" if os.name == "nt" else "/dev/null"


def build_video_args(plan_item: EncodePlanItem) -> list[str]:
    encoder = plan_item.encoder_info.encoder_name
    options = plan_item.options
    target_video_bps = plan_item.target_video_bitrate_bps
    args = [
        "-c:v",
        encoder,
        "-b:v",
        str(target_video_bps),
        "-pix_fmt",
        options.pix_fmt,
    ]

    # SVT-AV1 in current ffmpeg builds rejects VBV maxrate/bufsize in bitrate mode.
    if encoder != "libsvtav1":
        args += [
            "-maxrate",
            str(int(round(target_video_bps * options.maxrate_factor))),
            "-bufsize",
            str(int(round(target_video_bps * options.bufsize_factor))),
        ]

    if plan_item.options.encoder_preset:
        args += ["-preset", str(plan_item.options.encoder_preset)]

    if encoder == "libx265":
        args += ["-tag:v", "hvc1", "-x265-params", "log-level=error"]

    return args


def build_audio_args(plan_item: EncodePlanItem) -> list[str]:
    options = plan_item.options
    if options.audio_mode == AudioMode.COPY:
        return ["-map", "0:a?", "-c:a", "copy"]
    return ["-map", "0:a?", "-c:a", "aac", "-b:a", options.audio_bitrate]


def build_subtitle_args(plan_item: EncodePlanItem) -> list[str]:
    options = plan_item.options
    if not options.copy_subtitles:
        return []
    if options.container == ContainerChoice.MKV:
        return ["-map", "0:s?", "-c:s", "copy"]
    if options.container == ContainerChoice.MP4:
        return ["-map", "0:s?", "-c:s", "mov_text"]
    return []


def build_common_output_args(plan_item: EncodePlanItem) -> list[str]:
    args = ["-map_metadata", "0", "-map_chapters", "0"]
    if plan_item.options.container == ContainerChoice.MP4:
        args += ["-movflags", "+faststart"]
    return args


def build_encode_commands(
    ffmpeg_path: Path,
    plan_item: EncodePlanItem,
    workdir: Path,
    *,
    input_path: Path | None = None,
    output_path: Path | None = None,
    stage: str = "encode",
) -> tuple[list[list[str]], Path | None]:
    if not plan_item.encoder_info:
        raise ValueError("计划项缺少编码器信息。")

    source_path = input_path or plan_item.source_path
    final_output = output_path or plan_item.output_path
    overwrite_flag = "-y" if plan_item.options.overwrite or stage == "preview" else "-n"
    base_input = [str(ffmpeg_path), "-hide_banner", overwrite_flag, "-i", str(source_path)]

    video_args = build_video_args(plan_item)
    audio_args = build_audio_args(plan_item)
    subtitle_args = build_subtitle_args(plan_item)
    common_output_args = build_common_output_args(plan_item)

    if plan_item.options.two_pass and plan_item.encoder_info.supports_two_pass:
        passlog = passlog_prefix(workdir, plan_item.source_path, stage)
        pass1 = (
            base_input
            + ["-map", "0:v:0"]
            + video_args
            + ["-an", "-sn", "-dn", "-pass", "1", "-passlogfile", str(passlog), "-f", "null", _null_sink()]
        )
        pass2 = (
            base_input
            + ["-map", "0:v:0"]
            + audio_args
            + subtitle_args
            + video_args
            + common_output_args
            + ["-pass", "2", "-passlogfile", str(passlog), str(final_output)]
        )
        return [pass1, pass2], passlog

    cmd = (
        base_input
        + ["-map", "0:v:0"]
        + audio_args
        + subtitle_args
        + video_args
        + common_output_args
        + [str(final_output)]
    )
    return [cmd], None


def build_preview_extract_command(ffmpeg_path: Path, preview_job: PreviewJob) -> list[str]:
    return [
        str(ffmpeg_path),
        "-hide_banner",
        "-y",
        "-ss",
        f"{preview_job.start_sec:.3f}",
        "-t",
        f"{preview_job.duration_sec:.3f}",
        "-i",
        str(preview_job.source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map",
        "0:s?",
        "-c",
        "copy",
        str(preview_job.source_sample_path),
    ]


def build_preview_encode_commands(
    ffmpeg_path: Path,
    preview_job: PreviewJob,
    workdir: Path,
) -> tuple[list[list[str]], Path | None]:
    return build_encode_commands(
        ffmpeg_path=ffmpeg_path,
        plan_item=preview_job.plan_item,
        workdir=workdir,
        input_path=preview_job.source_sample_path,
        output_path=preview_job.encoded_sample_path,
        stage="preview",
    )
