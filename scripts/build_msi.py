from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.build_nuitka import normalize_version, project_root


ARCHITECTURES = {
    "x86_64": "x64",
    "arm64": "arm64",
}


def build_wix_command(
    *,
    version: str,
    architecture: str,
    source_dir: Path,
    output_path: Path,
    intermediate_dir: Path,
    icon_path: Path,
    wix_executable: str = "wix",
    wxs_path: Path | None = None,
) -> list[str]:
    if architecture not in ARCHITECTURES:
        raise ValueError(f"Unsupported MSI architecture: {architecture}")
    normalized_version = normalize_version(version)
    return [
        wix_executable,
        "build",
        str((wxs_path or project_root() / "packaging" / "windows" / "Product.wxs").resolve()),
        "-arch",
        ARCHITECTURES[architecture],
        "-d",
        f"Version={normalized_version}",
        "-d",
        f"SourceDir={source_dir.resolve()}",
        "-d",
        f"IconPath={icon_path.resolve()}",
        "-intermediateFolder",
        str(intermediate_dir.resolve()),
        "-pdbtype",
        "none",
        "-o",
        str(output_path.resolve()),
    ]


def validate_source(source_dir: Path, icon_path: Path) -> None:
    required = (
        source_dir / "video-compressor.exe",
        source_dir / "FFmpeg" / "bin" / "ffmpeg.exe",
        source_dir / "FFmpeg" / "bin" / "ffprobe.exe",
        icon_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("MSI source is incomplete: " + ", ".join(missing))


def _argument_parser() -> argparse.ArgumentParser:
    root = project_root()
    parser = argparse.ArgumentParser(description="Build the Windows MSI from a staged Nuitka directory.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--architecture", choices=tuple(ARCHITECTURES), required=True)
    parser.add_argument("--source-dir", type=Path, default=root / "dist" / "video-compressor")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--intermediate-dir",
        type=Path,
        default=root / "workdir" / "wix",
    )
    parser.add_argument("--icon", type=Path, default=root / "packaging" / "assets" / "app.ico")
    parser.add_argument("--wix", help="Path to wix.exe (defaults to PATH lookup)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    wix_executable = args.wix or shutil.which("wix")
    if not wix_executable:
        print("MSI build failed: wix executable was not found.", file=sys.stderr)
        return 2

    try:
        validate_source(args.source_dir.resolve(), args.icon.resolve())
        intermediate_dir = args.intermediate_dir.resolve() / args.architecture
        intermediate_dir.mkdir(parents=True, exist_ok=True)
        args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
        command = build_wix_command(
            version=args.version,
            architecture=args.architecture,
            source_dir=args.source_dir,
            output_path=args.output,
            intermediate_dir=intermediate_dir,
            icon_path=args.icon,
            wix_executable=wix_executable,
        )
        print("Running:", " ".join(command))
        subprocess.run(command, check=True, cwd=project_root())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"MSI build failed: {exc}", file=sys.stderr)
        return 2
    print(f"MSI package: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
