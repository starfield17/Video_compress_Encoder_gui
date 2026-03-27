from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def spec_path() -> Path:
    return project_root() / "packaging" / "video_compressor.spec"


def dist_dir() -> Path:
    return project_root() / "dist"


def packaging_dir() -> Path:
    return project_root() / "packaging"


def assets_dir() -> Path:
    return packaging_dir() / "assets"


def default_icon_path() -> Path | None:
    candidates = []
    if sys.platform.startswith("win"):
        candidates = [
            assets_dir() / "app.ico",
            assets_dir() / "icon.ico",
        ]
    elif sys.platform == "darwin":
        candidates = [
            assets_dir() / "app.icns",
            assets_dir() / "icon.icns",
        ]
    else:
        candidates = [
            assets_dir() / "app.png",
            assets_dir() / "icon.png",
        ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def default_version_file() -> Path | None:
    candidate = packaging_dir() / "windows_version_info.txt"
    if sys.platform.startswith("win") and candidate.exists():
        return candidate
    return None


def _add_data_arg(source: Path, target: str) -> str:
    separator = ";" if sys.platform.startswith("win") else ":"
    return f"{source}{separator}{target}"


def _uses_script_build(args: argparse.Namespace) -> bool:
    return bool(args.onefile or args.windowed or args.name)


def _pyinstaller_command(args: argparse.Namespace) -> tuple[list[str], str]:
    if _uses_script_build(args):
        icon_path = Path(args.icon).resolve() if args.icon else default_icon_path()
        version_file = Path(args.version_file).resolve() if args.version_file else default_version_file()
        output_name = args.name or "video-compressor"
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            str(project_root() / "main.py"),
            "--name",
            output_name,
            "--paths",
            str(project_root()),
            "--add-data",
            _add_data_arg(project_root() / "config", "config"),
        ]
        if args.clean:
            cmd.append("--clean")
        if args.windowed:
            cmd.append("--windowed")
        if args.onefile:
            cmd.append("--onefile")
        if not args.upx:
            cmd.append("--noupx")
        if icon_path is not None:
            cmd.extend(["--icon", str(icon_path)])
        if version_file is not None:
            cmd.extend(["--version-file", str(version_file)])
        return cmd, output_name

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        str(spec_path()),
    ]
    if args.clean:
        cmd.append("--clean")
    if not args.upx:
        cmd.append("--noupx")
    return cmd, "video-compressor"


def _copy_extra_files(target_dir: Path) -> None:
    root = project_root()
    readme = root / "README.md"
    if readme.exists():
        shutil.copy2(readme, target_dir / "README.md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the app with PyInstaller")
    parser.add_argument("--clean", action="store_true", help="Clean PyInstaller cache and build dirs before build")
    parser.add_argument("--onefile", action="store_true", help="Build as onefile instead of onedir")
    parser.add_argument("--windowed", action="store_true", help="Use the windowed bootloader")
    parser.add_argument("--name", help="Override the output executable name")
    parser.add_argument("--icon", help="Optional path to the application icon")
    parser.add_argument("--version-file", help="Optional Windows version metadata file")
    parser.add_argument("--upx", action=argparse.BooleanOptionalAction, default=True, help="Enable or disable UPX")
    args = parser.parse_args(argv)

    root = project_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    if not _uses_script_build(args):
        if args.icon:
            print("Ignoring --icon because spec-mode builds read the icon from packaging/video_compressor.spec")
        if args.version_file:
            print(
                "Ignoring --version-file because spec-mode builds read Windows version metadata "
                "from packaging/video_compressor.spec"
            )

    cmd, output_name = _pyinstaller_command(args)
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=root, env=env)

    if args.onefile:
        target_dir = dist_dir()
    else:
        target_dir = dist_dir() / output_name
    if target_dir.exists() and target_dir.is_dir():
        _copy_extra_files(target_dir)

    print(f"Build completed: {target_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
