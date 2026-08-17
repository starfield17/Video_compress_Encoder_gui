from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

from core.models import VmafCapabilities
from core.subprocess_utils import noninteractive_run_kwargs


PTS_RESET_FILTER = "settb=AVTB,setpts=PTS-STARTPTS"
COARSE_VMAF_SUBSAMPLE = 3
EXACT_VMAF_SUBSAMPLE = 1
MAX_VMAF_THREADS = 8
STANDARD_VMAF_MODEL = "vmaf_v0.6.1"
VMAF_4K_MODEL = "vmaf_4k_v0.6.1"


class InvalidVmafSubsample(ValueError):
    pass


def vmaf_model_name(*, is_4k: bool) -> str:
    return VMAF_4K_MODEL if is_4k else STANDARD_VMAF_MODEL


def validate_vmaf_subsample(n_subsample: int) -> int:
    """Reject even subsample values; Netflix warns they can be inaccurate."""
    value = int(n_subsample)
    if value < 1:
        raise InvalidVmafSubsample("VMAF n_subsample must be at least 1.")
    if value % 2 == 0:
        raise InvalidVmafSubsample(
            f"VMAF n_subsample={value} is even; even values can produce inaccurate scores."
        )
    return value


def vmaf_thread_budget(active_cpu_vmaf_jobs: int = 1) -> int:
    cpu_count = os.cpu_count() or 4
    reserve = 1 if cpu_count <= 4 else 2
    available = max(1, cpu_count - reserve)
    jobs = max(1, int(active_cpu_vmaf_jobs))
    return max(1, min(MAX_VMAF_THREADS, available // jobs))


def build_libvmaf_option(
    *,
    model: str,
    log_path: str,
    n_threads: int,
    n_subsample: int,
    filter_name: str = "libvmaf",
) -> str:
    subsample = validate_vmaf_subsample(n_subsample)
    threads = max(1, int(n_threads))
    return (
        f"{filter_name}=model=version={model}"
        f":n_threads={threads}"
        f":n_subsample={subsample}"
        f":log_fmt=json"
        f":log_path='{log_path}'"
    )


def build_cpu_vmaf_filter_graph(
    *,
    model: str,
    log_path: str,
    n_threads: int,
    n_subsample: int,
) -> str:
    libvmaf = build_libvmaf_option(
        model=model,
        log_path=log_path,
        n_threads=n_threads,
        n_subsample=n_subsample,
    )
    return f"[0:v]{PTS_RESET_FILTER}[dist];[1:v]{PTS_RESET_FILTER}[ref];[dist][ref]{libvmaf}"


def build_cpu_vmaf_command(
    ffmpeg_path: Path,
    *,
    distorted_path: Path,
    reference_path: Path,
    model: str,
    log_name: str,
    n_threads: int,
    n_subsample: int,
) -> list[str]:
    return [
        str(ffmpeg_path),
        "-hide_banner",
        "-y",
        "-i",
        str(distorted_path),
        "-i",
        str(reference_path),
        "-filter_complex",
        build_cpu_vmaf_filter_graph(
            model=model,
            log_path=log_name,
            n_threads=n_threads,
            n_subsample=n_subsample,
        ),
        "-an",
        "-f",
        "null",
        "-",
    ]


def build_cuda_vmaf_command(
    ffmpeg_path: Path,
    *,
    distorted_path: Path,
    reference_path: Path,
    model: str,
    log_name: str,
    n_threads: int,
    n_subsample: int,
) -> list[str]:
    libvmaf = build_libvmaf_option(
        model=model,
        log_path=log_name,
        n_threads=n_threads,
        n_subsample=n_subsample,
        filter_name="libvmaf_cuda",
    )
    return [
        str(ffmpeg_path),
        "-hide_banner",
        "-y",
        "-hwaccel",
        "cuda",
        "-hwaccel_output_format",
        "cuda",
        "-i",
        str(distorted_path),
        "-hwaccel",
        "cuda",
        "-hwaccel_output_format",
        "cuda",
        "-i",
        str(reference_path),
        "-filter_complex",
        f"[0:v]scale_cuda=format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[dist];"
        f"[1:v]scale_cuda=format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[ref];"
        f"[dist][ref]{libvmaf}",
        "-an",
        "-f",
        "null",
        "-",
    ]


def parse_vmaf_json(path: Path) -> float:
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        return float(data["pooled_metrics"]["vmaf"]["mean"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"VMAF JSON did not contain a pooled mean: {path}") from exc


def _run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **noninteractive_run_kwargs(),
    )


@lru_cache(maxsize=8)
def _detect_vmaf_cached(ffmpeg_path: str, size: int | None, mtime_ns: int | None) -> VmafCapabilities:
    del size, mtime_ns

    def model_works(model: str) -> tuple[bool, str]:
        cmd = [
            ffmpeg_path,
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=5:duration=0.4",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=5:duration=0.4",
            "-lavfi",
            f"libvmaf=model=version={model}",
            "-f",
            "null",
            "-",
        ]
        proc = _run_capture(cmd)
        return proc.returncode == 0 and "VMAF score:" in proc.stderr, proc.stderr

    standard, standard_error = model_works(STANDARD_VMAF_MODEL)
    if not standard:
        return VmafCapabilities(
            filter_available=False,
            standard_model=False,
            model_4k=False,
            error_message=standard_error.strip() or "libvmaf standard model could not run.",
        )
    model_4k, model_4k_error = model_works(VMAF_4K_MODEL)
    return VmafCapabilities(
        filter_available=True,
        standard_model=True,
        model_4k=model_4k,
        error_message=None if model_4k else (model_4k_error.strip() or "VMAF 4K model could not run."),
    )


def detect_vmaf_capabilities(ffmpeg_path: Path) -> VmafCapabilities:
    try:
        stat = ffmpeg_path.stat()
        size, mtime_ns = stat.st_size, stat.st_mtime_ns
    except OSError:
        size, mtime_ns = None, None
    return _detect_vmaf_cached(str(ffmpeg_path), size, mtime_ns)
