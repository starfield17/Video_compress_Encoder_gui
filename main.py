from __future__ import annotations

import sys

from cli.cli_entry import run_cli


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--gui":
        from gui.gui_entry import run_gui

        return run_gui(args[1:])
    if args and args[0] == "--cli":
        return run_cli(args[1:])
    if not args:
        from gui.gui_entry import run_gui

        return run_gui([])
    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())

