from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (
    (16, b"icp4"),
    (32, b"icp5"),
    (64, b"icp6"),
    (128, b"ic07"),
    (256, b"ic08"),
    (512, b"ic09"),
    (1024, b"ic10"),
)


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def render_png(svg_path: Path, size: int) -> bytes:
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG icon: {svg_path}")

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()

    encoded = QByteArray()
    buffer = QBuffer(encoded)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        raise RuntimeError(f"Could not encode {size}px PNG icon.")
    return bytes(encoded)


def build_ico(svg_path: Path) -> bytes:
    images = [(size, render_png(svg_path, size)) for size in ICO_SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries: list[bytes] = []
    payloads: list[bytes] = []
    for size, payload in images:
        dimension = 0 if size == 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        payloads.append(payload)
        offset += len(payload)
    return header + b"".join(entries) + b"".join(payloads)


def build_icns(svg_path: Path) -> bytes:
    chunks: list[bytes] = []
    for size, icon_type in ICNS_SIZES:
        payload = render_png(svg_path, size)
        chunks.append(icon_type + struct.pack(">I", len(payload) + 8) + payload)
    body = b"".join(chunks)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def generated_assets(svg_path: Path) -> dict[str, bytes]:
    return {
        "app.png": render_png(svg_path, 1024),
        "app.ico": build_ico(svg_path),
        "app.icns": build_icns(svg_path),
    }


def write_assets(svg_path: Path, output_dir: Path, *, check: bool = False) -> bool:
    expected = generated_assets(svg_path)
    mismatches: list[str] = []
    for name, payload in expected.items():
        path = output_dir / name
        if check:
            if not path.is_file() or path.read_bytes() != payload:
                mismatches.append(name)
            continue
        output_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    if mismatches:
        print(
            "Generated icon assets are stale or missing: " + ", ".join(mismatches),
            file=sys.stderr,
        )
        return False
    return True


def _argument_parser() -> argparse.ArgumentParser:
    root = project_root()
    parser = argparse.ArgumentParser(description="Generate PNG and ICO assets from the canonical SVG.")
    parser.add_argument("--svg", type=Path, default=root / "packaging" / "assets" / "app.svg")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "packaging" / "assets",
    )
    parser.add_argument("--check", action="store_true", help="Verify committed assets without changing them.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    return 0 if write_assets(args.svg.resolve(), args.output_dir.resolve(), check=args.check) else 1


if __name__ == "__main__":
    raise SystemExit(main())
