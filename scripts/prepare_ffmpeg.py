from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import BinaryIO

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from core.analysis_runtime import detect_analysis_capabilities, format_analysis_capability_report  # noqa: E402
from core.models import VmafBackend  # noqa: E402
from core.vmaf_runtime import VMAF_PRODUCTION_MODELS, build_vmaf_probe_command  # noqa: E402


USER_AGENT = "Video-compressor-release-ci/1.0"
MACHINE_TYPES = {
    "x86_64": {
        "pe": 0x8664,
        "elf": 0x3E,
        "macho": 0x01000007,
    },
    "arm64": {
        "pe": 0xAA64,
        "elf": 0xB7,
        "macho": 0x0100000C,
    },
}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def manifest_path() -> Path:
    return project_root() / "packaging" / "ffmpeg" / "manifest.json"


def load_manifest(path: Path | None = None) -> dict[str, object]:
    resolved = (path or manifest_path()).resolve()
    data = json.loads(resolved.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("targets"), dict):
        raise ValueError(f"Unsupported FFmpeg manifest: {resolved}")
    return data


def _require_workdir_path(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    workdir = (root / "workdir").resolve()
    try:
        resolved.relative_to(workdir)
    except ValueError as exc:
        raise ValueError(f"FFmpeg output must be inside {workdir}: {resolved}") from exc
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path, expected_sha256: str) -> Path:
    if destination.is_file() and sha256_file(destination) == expected_sha256:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists():
        partial.unlink()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        actual = sha256_file(partial)
        if actual != expected_sha256:
            raise RuntimeError(
                f"SHA-256 mismatch for {url}: expected {expected_sha256}, got {actual}"
            )
        partial.replace(destination)
    finally:
        if partial.exists():
            partial.unlink()
    return destination


def _archive_filename(url: str, digest: str) -> str:
    name = Path(urllib.parse.urlparse(url).path).name
    return f"{digest[:16]}-{name}"


def _copy_stream(source: BinaryIO, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        shutil.copyfileobj(source, output)
    destination.chmod(destination.stat().st_mode | 0o755)


def _wanted_binary(name: str, platform_name: str) -> str | None:
    basename = Path(name).name.lower()
    expected = {
        "ffmpeg.exe" if platform_name == "windows" else "ffmpeg",
        "ffprobe.exe" if platform_name == "windows" else "ffprobe",
    }
    return basename if basename in expected else None


def extract_binaries(archive: Path, archive_format: str, output_dir: Path, platform_name: str) -> set[str]:
    found: set[str] = set()
    if archive_format == "zip":
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                binary = _wanted_binary(member.filename, platform_name)
                if binary is None or binary in found or member.is_dir():
                    continue
                with package.open(member) as source:
                    _copy_stream(source, output_dir / "bin" / binary)
                found.add(binary)
        return found

    if archive_format == "tar.xz":
        with tarfile.open(archive, mode="r:xz") as package:
            for member in package.getmembers():
                binary = _wanted_binary(member.name, platform_name)
                if binary is None or binary in found or not member.isfile():
                    continue
                source = package.extractfile(member)
                if source is None:
                    continue
                with source:
                    _copy_stream(source, output_dir / "bin" / binary)
                found.add(binary)
        return found

    raise ValueError(f"Unsupported archive format: {archive_format}")


def binary_architecture(path: Path) -> str:
    data = path.read_bytes()[:4096]
    if data.startswith(b"MZ") and len(data) >= 64:
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if pe_offset + 6 > len(data):
            with path.open("rb") as handle:
                handle.seek(pe_offset)
                pe_header = handle.read(6)
        else:
            pe_header = data[pe_offset : pe_offset + 6]
        if pe_header[:4] != b"PE\0\0":
            raise ValueError(f"Invalid PE executable: {path}")
        machine = struct.unpack_from("<H", pe_header, 4)[0]
        binary_format = "pe"
    elif data.startswith(b"\x7fELF") and len(data) >= 20:
        endian = "<" if data[5] == 1 else ">"
        machine = struct.unpack_from(f"{endian}H", data, 18)[0]
        binary_format = "elf"
    elif data[:4] in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"} and len(data) >= 8:
        endian = "<" if data[:4] == b"\xcf\xfa\xed\xfe" else ">"
        machine = struct.unpack_from(f"{endian}I", data, 4)[0]
        binary_format = "macho"
    else:
        raise ValueError(f"Unsupported executable format: {path}")

    for architecture, formats in MACHINE_TYPES.items():
        if formats[binary_format] == machine:
            return architecture
    raise ValueError(f"Unsupported {binary_format} machine 0x{machine:x}: {path}")


def verify_binary_architecture(path: Path, expected: str) -> None:
    actual = binary_architecture(path)
    if actual != expected:
        raise RuntimeError(f"Architecture mismatch for {path}: expected {expected}, got {actual}")


def _run_checked(command: list[str]) -> str:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = result.stdout + "\n" + result.stderr
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{output}")
    return output


def verify_capabilities(ffmpeg_path: Path, ffprobe_path: Path) -> None:
    version = _run_checked([str(ffmpeg_path), "-hide_banner", "-version"])
    for option in ("--enable-gpl", "--enable-version3"):
        if option not in version:
            raise RuntimeError(f"Bundled FFmpeg is missing required configure option {option}.")

    filters = _run_checked([str(ffmpeg_path), "-hide_banner", "-filters"])
    if "libvmaf" not in filters:
        raise RuntimeError("Bundled FFmpeg does not provide the libvmaf filter.")

    encoders = _run_checked([str(ffmpeg_path), "-hide_banner", "-encoders"])
    for encoder in ("libx265", "libsvtav1"):
        if encoder not in encoders:
            raise RuntimeError(f"Bundled FFmpeg does not provide encoder {encoder}.")

    _run_checked([str(ffprobe_path), "-hide_banner", "-version"])
    for model_spec in VMAF_PRODUCTION_MODELS:
        try:
            output = _run_checked(
                build_vmaf_probe_command(ffmpeg_path, model_spec, VmafBackend.CPU)
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"VMAF v1 CPU smoke failed for model {model_spec.name}: {exc}"
            ) from exc
        if "VMAF score:" not in output:
            raise RuntimeError(
                f"VMAF v1 CPU smoke for model {model_spec.name} did not produce a score."
            )
    for encoder in ("libx265", "libsvtav1"):
        _run_checked(
            [
                str(ffmpeg_path),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=128x128:duration=0.2:rate=5",
                "-frames:v",
                "1",
                "-c:v",
                encoder,
                "-f",
                "null",
                "-",
            ]
        )

    try:
        print(format_analysis_capability_report(detect_analysis_capabilities(ffmpeg_path)))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Optional analysis capability report skipped: {exc}")


def _host_matches(target: dict[str, object]) -> bool:
    platform_name = str(target["platform"])
    architecture = str(target["architecture"])
    host_platform = (
        "windows"
        if sys.platform.startswith("win")
        else "macos"
        if sys.platform == "darwin"
        else "linux"
    )
    host_machine = platform.machine().lower()
    host_architecture = (
        "x86_64" if host_machine in {"amd64", "x86_64"} else "arm64" if host_machine in {"arm64", "aarch64"} else host_machine
    )
    return (platform_name, architecture) == (host_platform, host_architecture)


def prepare_target(
    target_name: str,
    output_dir: Path,
    *,
    manifest: dict[str, object] | None = None,
    root: Path | None = None,
    run_capability_checks: bool = True,
) -> Path:
    root = (root or project_root()).resolve()
    output_dir = _require_workdir_path(output_dir, root)
    data = manifest or load_manifest()
    targets = data["targets"]
    if not isinstance(targets, dict) or target_name not in targets:
        supported = ", ".join(sorted(targets)) if isinstance(targets, dict) else "none"
        raise ValueError(f"Unsupported FFmpeg target {target_name!r}; expected one of: {supported}")
    target = targets[target_name]
    if not isinstance(target, dict):
        raise ValueError(f"Invalid target entry: {target_name}")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    cache_dir = _require_workdir_path(root / "workdir" / "ffmpeg-cache", root)
    found: set[str] = set()
    for archive in target["archives"]:
        url = str(archive["url"])
        digest = str(archive["sha256"])
        cached = _download(url, cache_dir / _archive_filename(url, digest), digest)
        found.update(extract_binaries(cached, str(archive["format"]), output_dir, str(target["platform"])))

    suffix = ".exe" if target["platform"] == "windows" else ""
    expected_names = {f"ffmpeg{suffix}", f"ffprobe{suffix}"}
    if found != expected_names:
        raise RuntimeError(f"Incomplete FFmpeg pair for {target_name}: found {sorted(found)}")

    ffmpeg_path = output_dir / "bin" / f"ffmpeg{suffix}"
    ffprobe_path = output_dir / "bin" / f"ffprobe{suffix}"
    verify_binary_architecture(ffmpeg_path, str(target["architecture"]))
    verify_binary_architecture(ffprobe_path, str(target["architecture"]))

    licenses_dir = output_dir / "LICENSES"
    for license_entry in data["licenses"]:
        url = str(license_entry["url"])
        digest = str(license_entry["sha256"])
        cached = _download(url, cache_dir / _archive_filename(url, digest), digest)
        licenses_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, licenses_dir / str(license_entry["name"]))

    source_info = {
        "target": target_name,
        "ffmpeg_version": data["ffmpeg_version"],
        "provider": target["provider"],
        "source_version": target["source_version"],
        "build_recipe": target["build_recipe"],
        "ffmpeg_source": target["ffmpeg_source"],
        "archives": target["archives"],
    }
    (output_dir / "SOURCE.json").write_text(
        json.dumps(source_info, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "NOTICE.txt").write_text(
        "FFmpeg and FFprobe are separate GPLv3 programs bundled for convenience.\n"
        f"Version: {data['ffmpeg_version']}\n"
        f"Target: {target_name}\n"
        f"Binary provider: {target['provider']}\n"
        f"Corresponding source: {target['ffmpeg_source']}\n"
        f"Build recipe: {target['build_recipe']}\n"
        "See LICENSES/LICENSE.md and LICENSES/COPYING.GPLv3.\n",
        encoding="utf-8",
    )

    if run_capability_checks:
        if not _host_matches(target):
            raise RuntimeError(
                f"Capability checks for {target_name} require a matching native runner."
            )
        verify_capabilities(ffmpeg_path, ffprobe_path)
    return output_dir


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and verify the pinned FFmpeg release pair.")
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--skip-capability-check",
        action="store_true",
        help="Only validate hashes and binary architecture; intended for offline unit fixtures.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        destination = prepare_target(
            args.target,
            args.output,
            run_capability_checks=not args.skip_capability_check,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"FFmpeg preparation failed: {exc}", file=sys.stderr)
        return 2
    print(f"Prepared FFmpeg bundle: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
