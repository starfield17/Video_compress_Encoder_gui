from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_NAME = "video-compressor"
DEFAULT_OUTPUT_DIR = "dist"
DEFAULT_VERSION = "0.0.0"
_VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+){2,3}$")


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def normalize_version(version: str) -> str:
    """Validate a numeric release version and normalize it for Windows metadata."""
    value = version.strip()
    if not _VERSION_PATTERN.fullmatch(value):
        raise ValueError(
            f"Invalid version {version!r}; expected MAJOR.MINOR.PATCH or "
            "MAJOR.MINOR.PATCH.BUILD using numeric components."
        )
    components = value.split(".")
    if len(components) == 3:
        components.append("0")
    return ".".join(components)


def normalise_version(version: str) -> str:
    """British-spelling alias for callers that use the wording from the CLI docs."""
    return normalize_version(version)


def _is_windows(platform_name: str | None = None) -> bool:
    value = sys.platform if platform_name is None else platform_name
    return value.lower().startswith("win")


def _is_conda_python(python_executable: str) -> bool:
    executable = str(Path(python_executable).resolve()).lower()
    prefix = str(Path(sys.prefix).resolve()).lower()
    return bool(os.environ.get("CONDA_PREFIX")) or "conda" in executable or "conda" in prefix


def _validated_name(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("The executable name must be a non-empty filename without path separators.")
    return name


def _repo_path(value: str | Path, root: Path) -> Path:
    candidate = Path(value)
    resolved = (root / candidate if not candidate.is_absolute() else candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Output directory must be inside the repository: {resolved}") from exc
    return resolved


@dataclass(frozen=True)
class BuildPaths:
    root: Path
    output_dir: Path
    package_dir: Path
    nuitka_output_dir: Path
    reports_dir: Path
    report_path: Path
    executable_path: Path


def build_paths(
    *,
    name: str = DEFAULT_NAME,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    root: Path | None = None,
    platform_name: str | None = None,
) -> BuildPaths:
    root = (root or project_root()).resolve()
    name = _validated_name(name)
    resolved_output_dir = _repo_path(output_dir, root)
    executable_name = f"{name}.exe" if _is_windows(platform_name) else name
    return BuildPaths(
        root=root,
        output_dir=resolved_output_dir,
        package_dir=resolved_output_dir / name,
        nuitka_output_dir=root / "build" / "nuitka",
        reports_dir=root / "build" / "reports",
        report_path=root / "build" / "reports" / f"{name}.xml",
        executable_path=resolved_output_dir / name / executable_name,
    )


def final_package_dir(
    *,
    name: str = DEFAULT_NAME,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    root: Path | None = None,
) -> Path:
    """Return the normalized public package directory for a build."""
    return build_paths(name=name, output_dir=output_dir, root=root).package_dir


def _icon_path(root: Path) -> Path | None:
    for relative_path in (Path("packaging/assets/app.ico"), Path("packaging/assets/icon.ico")):
        candidate = root / relative_path
        if candidate.is_file():
            return candidate
    return None


def build_nuitka_command(
    version: str,
    *,
    name: str = DEFAULT_NAME,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    report_path: str | Path | None = None,
    root: Path | None = None,
    platform_name: str | None = None,
    python_executable: str | None = None,
    windows_compiler: str = "mingw64",
) -> list[str]:
    """Construct the single canonical Nuitka command used by local and CI builds."""
    normalized_version = normalize_version(version)
    paths = build_paths(name=name, output_dir=output_dir, root=root, platform_name=platform_name)
    executable = python_executable or sys.executable
    report = (
        Path(report_path)
        if report_path is not None
        else paths.report_path
    )
    if not report.is_absolute():
        report = paths.root / report
    report = report.resolve()
    command = [
        executable,
        "-m",
        "nuitka",
        "--mode=standalone",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        "--include-package=cli",
        "--include-package=core",
        "--include-package=gui",
        f"--output-dir={paths.nuitka_output_dir}",
        f"--output-filename={name}",
        f"--report={report}",
    ]

    if _is_conda_python(executable):
        # Conda does not ship libpython-static in a normal environment. Dynamic
        # libpython is sufficient for standalone output and keeps local Conda
        # builds usable without adding a Conda-only package requirement.
        command.append("--static-libpython=no")

    if _is_windows(platform_name):
        if windows_compiler == "mingw64":
            command.append("--mingw64")
        elif windows_compiler == "msvc":
            command.append("--msvc=latest")
        else:
            raise ValueError(f"Unsupported Windows compiler: {windows_compiler}")

        command.extend(
            [
                "--windows-console-mode=attach",
                "--product-name=Video Compressor",
                "--file-description=Video Compressor",
                "--company-name=starfield17",
                f"--product-version={normalized_version}",
                f"--file-version={normalized_version}",
            ]
        )
        icon_path = _icon_path(paths.root)
        if icon_path is not None:
            command.append(f"--windows-icon-from-ico={icon_path}")

    command.append("main.py")

    return command


def _remove_generated_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def clean_generated_paths(root: Path | None = None, package_dir: Path | None = None) -> None:
    """Remove only build output paths owned by this wrapper."""
    root = (root or project_root()).resolve()
    targets = [root / "build" / "nuitka", root / "build" / "reports", root / "dist" / DEFAULT_NAME]
    if package_dir is not None:
        resolved_package_dir = package_dir.resolve()
        try:
            resolved_package_dir.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Package directory must be inside the repository: {resolved_package_dir}") from exc
        if resolved_package_dir not in targets:
            targets.append(resolved_package_dir)
    for target in targets:
        _remove_generated_path(target)


def locate_distribution_dir(nuitka_output_dir: Path) -> Path:
    candidates = sorted(path for path in nuitka_output_dir.rglob("*.dist") if path.is_dir())
    if len(candidates) != 1:
        details = ", ".join(str(path) for path in candidates) or "none"
        raise RuntimeError(
            "Expected exactly one Nuitka standalone distribution directory in "
            f"{nuitka_output_dir}, found {len(candidates)}: {details}"
        )
    return candidates[0]


def find_ffmpeg_pair(ffmpeg_dir: Path, platform_name: str | None = None) -> tuple[Path, Path] | None:
    """Find a complete FFmpeg/FFprobe pair matching the target platform."""
    if _is_windows(platform_name):
        ffmpeg_name, ffprobe_name = "ffmpeg.exe", "ffprobe.exe"
    else:
        ffmpeg_name, ffprobe_name = "ffmpeg", "ffprobe"

    for relative_dir in (Path(), Path("bin")):
        ffmpeg_path = ffmpeg_dir / relative_dir / ffmpeg_name
        ffprobe_path = ffmpeg_dir / relative_dir / ffprobe_name
        if ffmpeg_path.is_file() and ffprobe_path.is_file():
            return ffmpeg_path, ffprobe_path
    return None


def stage_ffmpeg(
    root: Path,
    package_dir: Path,
    platform_name: str | None = None,
) -> bool:
    source_dir = root / "FFmpeg"
    target_dir = package_dir / "FFmpeg"
    pair = find_ffmpeg_pair(source_dir, platform_name)
    if pair is None:
        if target_dir.exists():
            _remove_generated_path(target_dir)
        expected = "ffmpeg.exe and ffprobe.exe" if _is_windows(platform_name) else "ffmpeg and ffprobe"
        print(f"Info: no complete compatible FFmpeg pair ({expected}) found under {source_dir}; skipping bundle.")
        return False

    if target_dir.exists():
        _remove_generated_path(target_dir)
    shutil.copytree(source_dir, target_dir)
    print(f"Staged FFmpeg bundle from {source_dir}.")
    return True


def stage_release_resources(
    package_dir: Path,
    *,
    root: Path | None = None,
    platform_name: str | None = None,
) -> None:
    root = (root or project_root()).resolve()
    package_dir = package_dir.resolve()
    package_dir.mkdir(parents=True, exist_ok=True)

    config_source = root / "config"
    readme_source = root / "README.md"
    if not config_source.is_dir():
        raise FileNotFoundError(f"Required release resource is missing: {config_source}")
    if not readme_source.is_file():
        raise FileNotFoundError(f"Required release resource is missing: {readme_source}")

    shutil.copytree(config_source, package_dir / "config", dirs_exist_ok=True)
    shutil.copy2(readme_source, package_dir / "README.md")
    stage_ffmpeg(root, package_dir, platform_name)


def _build_environment(root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(root) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    return environment


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the app with Nuitka standalone mode")
    parser.add_argument("--clean", action="store_true", help="Remove this wrapper's generated build output first")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Numeric release version")
    parser.add_argument("--name", default=DEFAULT_NAME, help="Executable and normalized package name")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Package output directory inside the repository")
    parser.add_argument(
        "--windows-compiler",
        choices=("mingw64", "msvc"),
        default="mingw64",
        help=(
            "Windows C compiler backend. 'mingw64' uses Nuitka's managed "
            "compiler; 'msvc' requires Visual Studio C++ Build Tools."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    root = project_root()
    try:
        normalized_version = normalize_version(args.version)
        paths = build_paths(
            name=args.name,
            output_dir=args.output_dir,
            root=root,
        )
    except ValueError as exc:
        print(f"Build configuration error: {exc}", file=sys.stderr)
        return 2

    if (
        sys.platform.startswith("win")
        and args.windows_compiler == "mingw64"
        and sys.version_info >= (3, 13)
    ):
        print(
            "Build configuration error: Nuitka MinGW64 builds require "
            "Python 3.12 or older. Use Python 3.12 or select "
            "--windows-compiler msvc with Visual Studio Build Tools.",
            file=sys.stderr,
        )
        return 2

    if args.clean:
        clean_generated_paths(root, paths.package_dir)

    paths.nuitka_output_dir.mkdir(parents=True, exist_ok=True)
    paths.reports_dir.mkdir(parents=True, exist_ok=True)
    command = build_nuitka_command(
        normalized_version,
        name=args.name,
        output_dir=args.output_dir,
        report_path=paths.report_path,
        root=root,
        windows_compiler=args.windows_compiler,
    )
    print("Running:", " ".join(command))
    subprocess.run(command, check=True, cwd=root, env=_build_environment(root))

    distribution_dir = locate_distribution_dir(paths.nuitka_output_dir)
    if paths.package_dir.exists():
        _remove_generated_path(paths.package_dir)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(distribution_dir), str(paths.package_dir))
    stage_release_resources(paths.package_dir, root=root)

    if not paths.executable_path.is_file():
        raise RuntimeError(f"Nuitka build completed but executable was not found: {paths.executable_path}")

    print(f"Final package: {paths.package_dir}")
    print(f"Executable: {paths.executable_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
