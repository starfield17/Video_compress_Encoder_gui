from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from cli.cli_interactive import print_encode_results, print_plan, print_preview_result
from core.exec_encode import execute_plan, execute_preview
from core.i18n import get_translator
from core.models import (
    AudioMode,
    BackendChoice,
    CodecChoice,
    ContainerChoice,
    EncodeOptions,
    PreviewOptions,
    PreviewSampleMode,
)
from core.plan_encode import build_encode_plan
from core.preset_store import (
    delete_preset,
    encode_options_to_preset_data,
    list_presets,
    load_app_config,
    load_preset,
    save_preset,
)
from core.preview_sample import build_preview_job


def _bool_action_kwargs() -> dict[str, object]:
    return {"action": argparse.BooleanOptionalAction, "default": None}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _config_dir() -> Path:
    return _repo_root() / "config"


def _default_workdir() -> Path:
    return _repo_root() / "workdir"


def _load_base_options(args: argparse.Namespace, config_dir: Path) -> EncodeOptions:
    preset_name = getattr(args, "preset", None)
    if preset_name:
        return load_preset(preset_name, config_dir)

    app_config = load_app_config(config_dir)
    default_preset_name = app_config.get("default_preset_name")
    if default_preset_name:
        try:
            return load_preset(default_preset_name, config_dir)
        except FileNotFoundError:
            pass
    return EncodeOptions()


def _merge_options(base: EncodeOptions, args: argparse.Namespace) -> EncodeOptions:
    updates: dict[str, object] = {}
    scalar_map = {
        "codec": lambda value: CodecChoice(value),
        "backend": lambda value: BackendChoice(value),
        "ratio": float,
        "min_video_kbps": int,
        "max_video_kbps": int,
        "container": lambda value: ContainerChoice(value),
        "audio_mode": lambda value: AudioMode(value),
        "audio_bitrate": str,
        "encoder_preset": str,
        "pix_fmt": str,
        "maxrate_factor": float,
        "bufsize_factor": float,
    }
    for name, caster in scalar_map.items():
        value = getattr(args, name, None)
        if value is not None:
            updates[name] = caster(value)

    bool_names = ["copy_subtitles", "copy_external_subtitles", "two_pass", "overwrite", "recursive", "dry_run"]
    for name in bool_names:
        value = getattr(args, name, None)
        if value is not None:
            updates[name] = bool(value)

    return replace(base, **updates)


def _options_from_args(args: argparse.Namespace, config_dir: Path) -> EncodeOptions:
    return _merge_options(_load_base_options(args, config_dir), args)


def _add_runtime_flags(parser: argparse.ArgumentParser, include_input: bool = True) -> None:
    if include_input:
        parser.add_argument("input", help="Input file or directory")
    parser.add_argument("-o", "--output", help="Output directory")
    parser.add_argument("--workdir", help="Working directory")
    parser.add_argument("--ffmpeg", help="Path to ffmpeg")
    parser.add_argument("--ffprobe", help="Path to ffprobe")
    parser.add_argument("--preset", help="Saved preset name")
    parser.add_argument("--lang", choices=["en", "zh_cn"], help="Language pack to load")
    parser.add_argument("--recursive", **_bool_action_kwargs(), help="Recursively scan subdirectories")


def _add_encode_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--codec", choices=["hevc", "av1"], help="Target codec")
    parser.add_argument("--backend", choices=["auto", "cpu", "nvenc", "amf"], help="Encoder backend")
    parser.add_argument("--ratio", type=float, help="Target video bitrate ratio")
    parser.add_argument("--min-video-kbps", dest="min_video_kbps", type=int, help="Minimum target bitrate")
    parser.add_argument("--max-video-kbps", dest="max_video_kbps", type=int, help="Maximum target bitrate")
    parser.add_argument("--container", choices=["mkv", "mp4"], help="Output container")
    parser.add_argument("--audio-mode", dest="audio_mode", choices=["copy", "aac"], help="Audio handling mode")
    parser.add_argument("--audio-bitrate", dest="audio_bitrate", help="Audio bitrate when re-encoding to AAC")
    parser.add_argument("--copy-subtitles", **_bool_action_kwargs(), help="Copy subtitle streams")
    parser.add_argument(
        "--copy-external-subtitles",
        dest="copy_external_subtitles",
        **_bool_action_kwargs(),
        help="Copy matching external subtitle files next to the encoded output",
    )
    parser.add_argument("--two-pass", dest="two_pass", **_bool_action_kwargs(), help="Enable two-pass mode")
    parser.add_argument("--overwrite", **_bool_action_kwargs(), help="Overwrite existing output")
    parser.add_argument("--encoder-preset", dest="encoder_preset", help="Concrete ffmpeg encoder preset")
    parser.add_argument("--pix-fmt", dest="pix_fmt", help="Output pixel format")
    parser.add_argument("--maxrate-factor", dest="maxrate_factor", type=float, help="maxrate factor")
    parser.add_argument("--bufsize-factor", dest="bufsize_factor", type=float, help="bufsize factor")
    parser.add_argument("--dry-run", action="store_true", help="Build the plan and stop")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Video compressor CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Preview the encode plan")
    _add_runtime_flags(plan_parser)
    _add_encode_flags(plan_parser)

    encode_parser = subparsers.add_parser("encode", help="Execute the encode plan")
    _add_runtime_flags(encode_parser)
    _add_encode_flags(encode_parser)

    preview_parser = subparsers.add_parser("preview", help="Run a manual preview sample")
    _add_runtime_flags(preview_parser)
    _add_encode_flags(preview_parser)
    preview_parser.add_argument("--sample-mode", choices=["middle", "custom"], default="middle")
    preview_parser.add_argument("--sample-duration", dest="sample_duration_sec", type=float, default=30.0)
    preview_parser.add_argument("--sample-start", dest="custom_start_sec", type=float)

    preset_parser = subparsers.add_parser("preset", help="Manage presets")
    preset_sub = preset_parser.add_subparsers(dest="preset_command", required=True)

    preset_list = preset_sub.add_parser("list", help="List available presets")
    preset_list.add_argument("--lang", choices=["en", "zh_cn"], help="Language pack to load")

    preset_load = preset_sub.add_parser("load", help="Print a preset")
    preset_load.add_argument("name")
    preset_load.add_argument("--lang", choices=["en", "zh_cn"], help="Language pack to load")

    preset_delete = preset_sub.add_parser("delete", help="Delete a preset")
    preset_delete.add_argument("name")
    preset_delete.add_argument("--lang", choices=["en", "zh_cn"], help="Language pack to load")

    preset_save = preset_sub.add_parser("save", help="Save a preset from current options")
    preset_save.add_argument("name")
    preset_save.add_argument("--preset", help="Base preset name")
    preset_save.add_argument("--lang", choices=["en", "zh_cn"], help="Language pack to load")
    _add_encode_flags(preset_save)

    return parser


def _translator_for_args(args: argparse.Namespace, config_dir: Path):
    app_config = load_app_config(config_dir)
    language = getattr(args, "lang", None) or app_config.get("language", "en")
    return get_translator(language, config_dir)


def _first_valid_plan_item(plan):
    for item in plan.items:
        if not item.skip_reason:
            return item
    return None


def _run_plan(args: argparse.Namespace, config_dir: Path) -> int:
    tr = _translator_for_args(args, config_dir)
    options = _options_from_args(args, config_dir)
    plan = build_encode_plan(
        input_path=Path(args.input),
        options=options,
        output_dir=Path(args.output).expanduser().resolve() if args.output else None,
        workdir=Path(args.workdir).expanduser().resolve() if args.workdir else _default_workdir(),
        ffmpeg_path=args.ffmpeg,
        ffprobe_path=args.ffprobe,
    )
    print_plan(plan, tr)
    return 0


def _run_encode(args: argparse.Namespace, config_dir: Path) -> int:
    tr = _translator_for_args(args, config_dir)
    options = _options_from_args(args, config_dir)
    plan = build_encode_plan(
        input_path=Path(args.input),
        options=options,
        output_dir=Path(args.output).expanduser().resolve() if args.output else None,
        workdir=Path(args.workdir).expanduser().resolve() if args.workdir else _default_workdir(),
        ffmpeg_path=args.ffmpeg,
        ffprobe_path=args.ffprobe,
    )
    print_plan(plan, tr)
    if options.dry_run:
        return 0
    results = execute_plan(plan, Path(args.workdir).expanduser().resolve() if args.workdir else _default_workdir())
    print_encode_results(results, tr)
    return 0 if all(result.success or result.skipped for result in results) else 2


def _run_preview(args: argparse.Namespace, config_dir: Path) -> int:
    tr = _translator_for_args(args, config_dir)
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        print(tr.t("cli.preview_requires_file"), file=sys.stderr)
        return 2

    options = _options_from_args(args, config_dir)
    plan = build_encode_plan(
        input_path=input_path,
        options=options,
        output_dir=Path(args.output).expanduser().resolve() if args.output else None,
        workdir=Path(args.workdir).expanduser().resolve() if args.workdir else _default_workdir(),
        ffmpeg_path=args.ffmpeg,
        ffprobe_path=args.ffprobe,
    )
    item = _first_valid_plan_item(plan)
    if item is None:
        print(tr.t("cli.preview_no_valid_item"), file=sys.stderr)
        print_plan(plan, tr)
        return 2

    preview_options = PreviewOptions(
        sample_mode=PreviewSampleMode(args.sample_mode),
        sample_duration_sec=args.sample_duration_sec,
        custom_start_sec=args.custom_start_sec,
    )
    job = build_preview_job(
        plan_item=item,
        workdir=Path(args.workdir).expanduser().resolve() if args.workdir else _default_workdir(),
        preview_options=preview_options,
    )
    result = execute_preview(
        job=job,
        ffmpeg_path=plan.ffmpeg_path,
        workdir=Path(args.workdir).expanduser().resolve() if args.workdir else _default_workdir(),
    )
    print_preview_result(result, tr)
    return 0 if result.success else 2


def _run_preset(args: argparse.Namespace, config_dir: Path) -> int:
    tr = _translator_for_args(args, config_dir)
    if args.preset_command == "list":
        names = list_presets(config_dir)
        if not names:
            print(tr.t("cli.no_presets"))
            return 0
        for name in names:
            print(name)
        return 0

    if args.preset_command == "load":
        options = load_preset(args.name, config_dir)
        print(json.dumps(encode_options_to_preset_data(options), indent=2, ensure_ascii=False))
        return 0

    if args.preset_command == "delete":
        delete_preset(args.name, config_dir)
        print(tr.t("cli.preset_deleted", name=args.name))
        return 0

    if args.preset_command == "save":
        options = _options_from_args(args, config_dir)
        path = save_preset(args.name, options, config_dir)
        print(tr.t("cli.preset_saved", name=args.name, path=path))
        return 0

    raise ValueError(f"Unsupported preset command: {args.preset_command}")


def run_cli(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config_dir = _config_dir()

    try:
        if args.command == "plan":
            return _run_plan(args, config_dir)
        if args.command == "encode":
            return _run_encode(args, config_dir)
        if args.command == "preview":
            return _run_preview(args, config_dir)
        if args.command == "preset":
            return _run_preset(args, config_dir)
    except Exception as exc:
        tr = _translator_for_args(args, config_dir)
        print(f"{tr.t('cli.error')}: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 1
