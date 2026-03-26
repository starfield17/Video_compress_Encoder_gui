from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from gui.gui_mainwindow import MainWindow


def run_gui(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Video compressor GUI")
    parser.add_argument("--lang", choices=["en", "zh_cn"], help="Language pack to load")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1] + (argv or []))
    window = MainWindow(repo_root=Path(__file__).resolve().parent.parent, language=args.lang)
    window.resize(1280, 860)
    window.show()
    return app.exec()
