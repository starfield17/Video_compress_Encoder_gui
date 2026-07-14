from __future__ import annotations

import shutil
import sys
from pathlib import Path


def is_compiled() -> bool:
    return bool(getattr(sys, "frozen", False) or globals().get("__compiled__") is not None)


def is_frozen() -> bool:
    """Compatibility name for callers that only need compiled-build detection."""
    return is_compiled()


def source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    if is_compiled():
        return Path(sys.executable).resolve().parent
    return source_root()


def app_root() -> Path:
    if is_compiled():
        return Path(sys.executable).resolve().parent
    return source_root()


def config_dir() -> Path:
    return app_root() / "config"


def workdir_dir() -> Path:
    return app_root() / "workdir"


def _copy_tree_if_missing(source_dir: Path, target_dir: Path) -> None:
    # Copy files from source to target only when they don't already exist in target.
    # Directories are always created; only leaf files are checked for existence.
    if not source_dir.exists():
        return
    for item in source_dir.rglob("*"):
        relative = item.relative_to(source_dir)
        target = target_dir / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def ensure_runtime_layout() -> tuple[Path, Path]:
    # Seeds the writable runtime directory from the bundled (potentially read-only) config.
    # Returns (runtime_config_dir, runtime_workdir).
    runtime_root = app_root()
    runtime_root.mkdir(parents=True, exist_ok=True)

    runtime_config = config_dir()
    runtime_workdir = workdir_dir()
    runtime_config.mkdir(parents=True, exist_ok=True)
    runtime_workdir.mkdir(parents=True, exist_ok=True)

    bundled_config = bundle_root() / "config"
    _copy_tree_if_missing(bundled_config, runtime_config)

    for name in ("preview", "logs", "temp"):
        (runtime_workdir / name).mkdir(parents=True, exist_ok=True)

    return runtime_config, runtime_workdir
