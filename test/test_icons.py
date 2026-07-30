from __future__ import annotations

import struct
import unittest
from pathlib import Path

from PySide6.QtSvg import QSvgRenderer

from scripts.build_icons import ICNS_SIZES, ICO_SIZES, write_assets


ROOT = Path(__file__).resolve().parent.parent


class IconAssetTestCase(unittest.TestCase):
    def test_svg_is_valid_and_generated_assets_are_current(self) -> None:
        svg_path = ROOT / "packaging" / "assets" / "app.svg"
        renderer = QSvgRenderer(str(svg_path))
        self.assertTrue(renderer.isValid())
        self.assertEqual(renderer.viewBox().width(), 1024)
        self.assertEqual(renderer.viewBox().height(), 1024)

        self.assertTrue(write_assets(svg_path, svg_path.parent, check=True))

    def test_ico_contains_expected_sizes(self) -> None:
        payload = (ROOT / "packaging" / "assets" / "app.ico").read_bytes()
        reserved, image_type, count = struct.unpack_from("<HHH", payload)
        self.assertEqual((reserved, image_type, count), (0, 1, len(ICO_SIZES)))

        sizes: list[int] = []
        for index in range(count):
            width, height = struct.unpack_from("<BB", payload, 6 + index * 16)
            self.assertEqual(width, height)
            sizes.append(256 if width == 0 else width)
        self.assertEqual(tuple(sizes), ICO_SIZES)

    def test_icns_contains_expected_png_chunks(self) -> None:
        payload = (ROOT / "packaging" / "assets" / "app.icns").read_bytes()
        self.assertEqual(payload[:4], b"icns")
        self.assertEqual(struct.unpack_from(">I", payload, 4)[0], len(payload))
        for _size, icon_type in ICNS_SIZES:
            self.assertIn(icon_type, payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
