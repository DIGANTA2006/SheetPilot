"""SheetPilot desktop entry point."""

from __future__ import annotations

import argparse
import multiprocessing
import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from sheetpilot.app.bootstrap import build_context, build_main_window


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SheetPilot desktop application")
    parser.add_argument("--version", action="store_true", help="print the application version")
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    options = parse_arguments(arguments)
    if options.version:
        from sheetpilot.app.version import __version__

        print(__version__)
        return 0
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = QApplication(sys.argv[:1])
    context = build_context()
    window = build_main_window(application, context)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
