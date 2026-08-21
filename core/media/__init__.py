"""Media discovery, preview, bitrate, and output-file contracts."""

from core.media.bitrate import DEFAULT_RATIO, human_kbps
from core.media.discovery import collect_video_files
from core.media.metadata import infer_bit_depth_from_pix_fmt
from core.media.preview import build_preview_job
from core.media.skipped import (
    group_skipped_output_pairs,
    is_eligible_skipped_item,
    publish_skipped_source,
    publish_skipped_sources,
)

__all__ = [
    "DEFAULT_RATIO",
    "build_preview_job",
    "collect_video_files",
    "group_skipped_output_pairs",
    "human_kbps",
    "infer_bit_depth_from_pix_fmt",
    "is_eligible_skipped_item",
    "publish_skipped_source",
    "publish_skipped_sources",
]
