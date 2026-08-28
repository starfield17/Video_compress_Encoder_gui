from __future__ import annotations

import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from core.media import (
    SpaceSavingsItem,
    SpaceSavingsOutcome,
    SpaceSavingsSummary,
    calculate_space_savings,
)


class SpaceSavingsTestCase(unittest.TestCase):
    def test_empty_records(self) -> None:
        summary = calculate_space_savings([])
        self.assertEqual(summary.total_files, 0)
        self.assertEqual(summary.successful_files, 0)
        self.assertEqual(summary.failed_files, 0)
        self.assertEqual(summary.skipped_files, 0)
        self.assertEqual(summary.needs_decision_files, 0)
        self.assertEqual(summary.cancelled_files, 0)
        self.assertEqual(summary.original_total_bytes, 0)
        self.assertEqual(summary.compressed_total_bytes, 0)
        self.assertEqual(summary.saved_bytes, 0)
        self.assertEqual(summary.saved_ratio, 0.0)
        self.assertEqual(summary.total_elapsed_sec, 0.0)

    def test_mixed_outcomes_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src1 = tmp_path / "src1.mov"
            src2 = tmp_path / "src2.mov"
            src3 = tmp_path / "src3.mov"
            src4 = tmp_path / "src4.mov"
            src5 = tmp_path / "src5.mov"
            src6 = tmp_path / "src6.mov"

            src1.write_bytes(b"a" * (100 * 1024 * 1024))
            src2.write_bytes(b"b" * (50 * 1024 * 1024))
            src3.write_bytes(b"c" * (20 * 1024 * 1024))
            src4.write_bytes(b"d" * (30 * 1024 * 1024))
            src5.write_bytes(b"e" * (40 * 1024 * 1024))
            src6.write_bytes(b"f" * (15 * 1024 * 1024))

            items = [
                # Item 1: Success, 100MB -> 40MB (Saved 60MB) via actual_output_bytes
                SpaceSavingsItem(
                    outcome=SpaceSavingsOutcome.SUCCESS,
                    source_path=src1,
                    actual_output_bytes=40 * 1024 * 1024,
                ),
                # Item 2: Success, 50MB -> 10MB (Saved 40MB) via actual_output_bytes
                SpaceSavingsItem(
                    outcome=SpaceSavingsOutcome.SUCCESS,
                    source_path=src2,
                    actual_output_bytes=10 * 1024 * 1024,
                ),
                # Item 3: Failed, 20MB
                SpaceSavingsItem(
                    outcome=SpaceSavingsOutcome.FAILED,
                    source_path=src3,
                ),
                # Item 4: Skipped, 30MB
                SpaceSavingsItem(
                    outcome=SpaceSavingsOutcome.SKIPPED,
                    source_path=src4,
                ),
                # Item 5: Needs Decision, 40MB
                SpaceSavingsItem(
                    outcome=SpaceSavingsOutcome.NEEDS_DECISION,
                    source_path=src5,
                ),
                # Item 6: Cancelled, 15MB
                SpaceSavingsItem(
                    outcome=SpaceSavingsOutcome.CANCELLED,
                    source_path=src6,
                ),
            ]

            summary = calculate_space_savings(items, total_elapsed_sec=120.0)

            self.assertEqual(summary.total_files, 6)
            self.assertEqual(summary.successful_files, 2)
            self.assertEqual(summary.failed_files, 1)
            self.assertEqual(summary.skipped_files, 1)
            self.assertEqual(summary.needs_decision_files, 1)
            self.assertEqual(summary.cancelled_files, 1)

            expected_orig = (100 + 50) * 1024 * 1024
            expected_comp = (40 + 10) * 1024 * 1024
            expected_saved = (60 + 40) * 1024 * 1024

            self.assertEqual(summary.original_total_bytes, expected_orig)
            self.assertEqual(summary.compressed_total_bytes, expected_comp)
            self.assertEqual(summary.saved_bytes, expected_saved)
            self.assertAlmostEqual(summary.saved_ratio, 100.0 / 150.0, places=4)
            self.assertEqual(summary.total_elapsed_sec, 120.0)

    def test_actual_output_bytes_precedence_over_disk_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "in.mp4"
            out = tmp_path / "out.mp4"
            src.write_bytes(b"x" * 1000)
            out.write_bytes(b"y" * 200)

            # Explicit actual_output_bytes=500 should override disk file size 200
            item = SpaceSavingsItem(
                outcome=SpaceSavingsOutcome.SUCCESS,
                source_path=src,
                output_path=out,
                actual_output_bytes=500,
            )
            summary = calculate_space_savings([item])
            self.assertEqual(summary.original_total_bytes, 1000)
            self.assertEqual(summary.compressed_total_bytes, 500)
            self.assertEqual(summary.saved_bytes, 500)
            self.assertEqual(summary.saved_ratio, 0.5)

    def test_disk_file_fallback_when_actual_output_bytes_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "in.mp4"
            out = tmp_path / "out.mp4"
            src.write_bytes(b"x" * 1000)
            out.write_bytes(b"y" * 250)

            item = SpaceSavingsItem(
                outcome=SpaceSavingsOutcome.SUCCESS,
                source_path=src,
                output_path=out,
                actual_output_bytes=None,
            )
            summary = calculate_space_savings([item])
            self.assertEqual(summary.original_total_bytes, 1000)
            self.assertEqual(summary.compressed_total_bytes, 250)
            self.assertEqual(summary.saved_bytes, 750)
            self.assertEqual(summary.saved_ratio, 0.75)

    def test_missing_or_unreadable_files(self) -> None:
        missing_src = Path("/nonexistent/file/path_src.mov")
        missing_out = Path("/nonexistent/file/path_out.mp4")

        item = SpaceSavingsItem(
            outcome=SpaceSavingsOutcome.SUCCESS,
            source_path=missing_src,
            output_path=missing_out,
            actual_output_bytes=None,
        )
        summary = calculate_space_savings([item])
        self.assertEqual(summary.total_files, 1)
        self.assertEqual(summary.successful_files, 1)
        self.assertEqual(summary.original_total_bytes, 0)
        self.assertEqual(summary.compressed_total_bytes, 0)
        self.assertEqual(summary.saved_bytes, 0)
        self.assertEqual(summary.saved_ratio, 0.0)

    def test_file_growth_negative_savings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "in.mp4"
            src.write_bytes(b"x" * 100)

            # Output is larger than input (150 > 100)
            item = SpaceSavingsItem(
                outcome=SpaceSavingsOutcome.SUCCESS,
                source_path=src,
                actual_output_bytes=150,
            )
            summary = calculate_space_savings([item])
            self.assertEqual(summary.original_total_bytes, 100)
            self.assertEqual(summary.compressed_total_bytes, 150)
            self.assertEqual(summary.saved_bytes, -50)
            self.assertEqual(summary.saved_ratio, -0.5)

    def test_dataclass_immutability(self) -> None:
        item = SpaceSavingsItem(outcome=SpaceSavingsOutcome.SUCCESS, actual_output_bytes=10)
        with self.assertRaises(FrozenInstanceError):
            item.actual_output_bytes = 20  # type: ignore[misc]

        summary = SpaceSavingsSummary(
            total_files=1,
            successful_files=1,
            failed_files=0,
            skipped_files=0,
            needs_decision_files=0,
            cancelled_files=0,
            original_total_bytes=100,
            compressed_total_bytes=50,
            saved_bytes=50,
            saved_ratio=0.5,
            total_elapsed_sec=10.0,
        )
        with self.assertRaises(FrozenInstanceError):
            summary.total_files = 2  # type: ignore[misc]

if __name__ == "__main__":
    unittest.main(verbosity=2)
