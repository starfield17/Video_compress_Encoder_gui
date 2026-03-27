from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class CodecChoice(str, Enum):
    HEVC = "hevc"
    AV1 = "av1"


class BackendChoice(str, Enum):
    AUTO = "auto"
    CPU = "cpu"
    NVENC = "nvenc"
    QSV = "qsv"
    AMF = "amf"


class AudioMode(str, Enum):
    COPY = "copy"
    AAC = "aac"


class ContainerChoice(str, Enum):
    MKV = "mkv"
    MP4 = "mp4"


class PreviewSampleMode(str, Enum):
    MIDDLE = "middle"
    CUSTOM = "custom"


class OperationCancelledError(RuntimeError):
    """Raised when a running planning/preview/encode task is cancelled."""


@dataclass(slots=True)
class VideoFileItem:
    path: Path
    relative_path: Path


@dataclass(slots=True)
class MediaInfo:
    path: Path
    duration: float
    format_bitrate_bps: int
    video_bitrate_bps: int
    audio_bitrate_bps: int
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    video_codec: str
    audio_codec: Optional[str]


@dataclass(slots=True)
class EncodeOptions:
    codec: CodecChoice = CodecChoice.HEVC
    backend: BackendChoice = BackendChoice.AUTO
    ratio: Optional[float] = None
    min_video_kbps: int = 250
    max_video_kbps: int = 0
    container: ContainerChoice = ContainerChoice.MKV
    audio_mode: AudioMode = AudioMode.COPY
    audio_bitrate: str = "128k"
    copy_subtitles: bool = False
    copy_external_subtitles: bool = False
    two_pass: bool = False
    encoder_preset: Optional[str] = None
    pix_fmt: str = "yuv420p"
    maxrate_factor: float = 1.08
    bufsize_factor: float = 2.0
    overwrite: bool = False
    recursive: bool = False
    dry_run: bool = False


@dataclass(slots=True)
class EncoderInfo:
    codec: CodecChoice
    backend: BackendChoice
    encoder_name: str
    supports_two_pass: bool
    default_preset: Optional[str]


@dataclass(slots=True)
class EncodePlanItem:
    source_path: Path
    output_path: Path
    media_info: Optional[MediaInfo]
    encoder_info: Optional[EncoderInfo]
    options: EncodeOptions
    target_video_bitrate_bps: int = 0
    warnings: list[str] = field(default_factory=list)
    skip_reason: Optional[str] = None


@dataclass(slots=True)
class EncodePlan:
    items: list[EncodePlanItem]
    ffmpeg_path: Path
    ffprobe_path: Path
    input_root: Path
    output_root: Path
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EncodeResult:
    source_path: Path
    output_path: Path
    success: bool
    return_code: int = 0
    commands: list[list[str]] = field(default_factory=list)
    log_path: Optional[Path] = None
    error_message: Optional[str] = None
    skipped: bool = False
    copied_external_subtitle_paths: list[Path] = field(default_factory=list)
    external_subtitle_warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PreviewOptions:
    sample_mode: PreviewSampleMode = PreviewSampleMode.MIDDLE
    sample_duration_sec: float = 30.0
    custom_start_sec: Optional[float] = None


@dataclass(slots=True)
class PreviewJob:
    source_path: Path
    source_sample_path: Path
    encoded_sample_path: Path
    start_sec: float
    duration_sec: float
    plan_item: EncodePlanItem
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PreviewResult:
    job: PreviewJob
    success: bool
    source_sample_size: int = 0
    encoded_sample_size: int = 0
    sample_compression_ratio: float = 0.0
    estimated_full_output_size: int = 0
    notes: list[str] = field(default_factory=list)
    log_path: Optional[Path] = None
    error_message: Optional[str] = None
