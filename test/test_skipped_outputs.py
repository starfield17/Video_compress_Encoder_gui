from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.models import (
    BackendChoice,
    CodecChoice,
    EncodeOptions,
    EncodePlanItem,
    EncodeResult,
    EncoderInfo,
    QualityUnreachablePolicy,
)
from core.skipped_outputs import (
    is_eligible_skipped_item,
    publish_skipped_source,
    publish_skipped_sources,
)


def _item(root: Path, *, skip_reason: str | None = None, overwrite: bool = True) -> EncodePlanItem:
    source = root / "source.mov"
    source.write_bytes(b"source-bytes")
    return EncodePlanItem(
        source_path=source,
        output_path=root / "out" / "source_hevc.mp4",
        media_info=None,
        encoder_info=EncoderInfo(
            codec=CodecChoice.HEVC,
            backend=BackendChoice.CPU,
            encoder_name="libx265",
            supports_two_pass=True,
            default_preset="slow",
        ),
        options=EncodeOptions(
            overwrite=overwrite,
            quality_unreachable_policy=QualityUnreachablePolicy.SKIP,
        ),
        skip_reason=skip_reason,
    )


class SkippedOutputPublishTestCase(unittest.TestCase):
    def test_analysis_skip_is_copied_to_planned_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            item = _item(root)
            result = EncodeResult(
                source_path=item.source_path,
                output_path=item.output_path,
                success=False,
                skipped=True,
            )
            self.assertTrue(is_eligible_skipped_item(item, result))
            published = publish_skipped_source(item)
            self.assertTrue(published.copied)
            self.assertEqual(item.output_path.read_bytes(), b"source-bytes")

    def test_planning_skip_is_not_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            item = _item(Path(temp_dir), skip_reason="probe failed")
            result = EncodeResult(
                source_path=item.source_path,
                output_path=item.output_path,
                success=False,
                skipped=True,
                error_message="probe failed",
            )
            self.assertFalse(is_eligible_skipped_item(item, result))
            self.assertEqual(publish_skipped_sources([(item, result)]), [])
            self.assertFalse(item.output_path.exists())

    def test_existing_output_without_overwrite_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            item = _item(root, overwrite=False)
            item.output_path.parent.mkdir()
            item.output_path.write_bytes(b"existing")
            published = publish_skipped_source(item)
            self.assertFalse(published.copied)
            self.assertEqual(published.reason, "output exists")
            self.assertEqual(item.output_path.read_bytes(), b"existing")

    def test_ignore_policy_does_not_change_eligibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            item = _item(Path(temp_dir))
            result = EncodeResult(
                source_path=item.source_path,
                output_path=item.output_path,
                success=False,
                skipped=True,
            )
            self.assertTrue(is_eligible_skipped_item(item, result))


if __name__ == "__main__":
    unittest.main()
