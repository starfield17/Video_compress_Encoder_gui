from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from core.app_paths import app_root, ensure_runtime_layout
from gui.gui_mainwindow import MainWindow


def run_gui(argv: list[str] | None = None) -> int:
    ensure_runtime_layout()
    parser = argparse.ArgumentParser(description="Video compressor GUI")
    parser.add_argument("--lang", choices=["en", "zh_cn"], help="Language pack to load")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1] + (argv or []))
    window = MainWindow(repo_root=app_root(), language=args.lang)
    window.show()
    return app.exec()
