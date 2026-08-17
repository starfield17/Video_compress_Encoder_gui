from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from core.vmaf_runtime import (
    COARSE_VMAF_SUBSAMPLE,
    EXACT_VMAF_SUBSAMPLE,
    InvalidVmafSubsample,
    PTS_RESET_FILTER,
    build_cpu_vmaf_command,
    build_cuda_vmaf_command,
    build_libvmaf_option,
    validate_vmaf_subsample,
    vmaf_thread_budget,
)


class VmafRuntimeTestCase(unittest.TestCase):
    def test_exact_and_coarse_subsamples_are_odd(self) -> None:
        self.assertEqual(validate_vmaf_subsample(EXACT_VMAF_SUBSAMPLE), 1)
        self.assertEqual(validate_vmaf_subsample(COARSE_VMAF_SUBSAMPLE), 3)
        with self.assertRaises(InvalidVmafSubsample):
            validate_vmaf_subsample(2)
        with self.assertRaises(InvalidVmafSubsample):
            validate_vmaf_subsample(0)

    def test_thread_budget_is_at_least_one_and_respects_active_jobs(self) -> None:
        with patch("core.vmaf_runtime.os.cpu_count", return_value=16):
            self.assertEqual(vmaf_thread_budget(1), 8)
            self.assertEqual(vmaf_thread_budget(2), 7)
        with patch("core.vmaf_runtime.os.cpu_count", return_value=4):
            self.assertEqual(vmaf_thread_budget(1), 3)
        with patch("core.vmaf_runtime.os.cpu_count", return_value=None):
            self.assertGreaterEqual(vmaf_thread_budget(1), 1)

    def test_cpu_command_uses_official_pts_reset_and_thread_options(self) -> None:
        command = build_cpu_vmaf_command(
            Path("ffmpeg"),
            distorted_path=Path("dist.mkv"),
            reference_path=Path("ref.mkv"),
            model="vmaf_v0.6.1",
            log_name="vmaf.json",
            n_threads=8,
            n_subsample=3,
        )
        graph = command[command.index("-filter_complex") + 1]
        self.assertEqual(PTS_RESET_FILTER, "settb=AVTB,setpts=PTS-STARTPTS")
        self.assertIn(PTS_RESET_FILTER, graph)
        self.assertIn("n_threads=8", graph)
        self.assertIn("n_subsample=3", graph)
        self.assertIn("libvmaf=", graph)
        self.assertNotIn("n_subsample=2", graph)

    def test_cuda_command_requires_cuda_frames_and_libvmaf_cuda(self) -> None:
        command = build_cuda_vmaf_command(
            Path("ffmpeg"),
            distorted_path=Path("dist.mkv"),
            reference_path=Path("ref.mkv"),
            model="vmaf_v0.6.1",
            log_name="vmaf.json",
            n_threads=4,
            n_subsample=1,
        )
        self.assertEqual(command.count("-hwaccel"), 2)
        self.assertIn("cuda", command)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("scale_cuda", graph)
        self.assertIn("libvmaf_cuda=", graph)
        self.assertIn("n_subsample=1", graph)

    def test_even_subsample_cannot_be_rendered(self) -> None:
        with self.assertRaises(InvalidVmafSubsample):
            build_libvmaf_option(
                model="vmaf_v0.6.1",
                log_path="vmaf.json",
                n_threads=1,
                n_subsample=2,
            )


if __name__ == "__main__":
    unittest.main()
