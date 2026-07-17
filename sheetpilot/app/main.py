"""SheetPilot desktop entry point."""

from __future__ import annotations

import argparse
import multiprocessing
import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication, QWidget

from sheetpilot.app.bootstrap import build_context, build_main_window


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SheetPilot desktop application")
    launch_mode = parser.add_mutually_exclusive_group()
    launch_mode.add_argument("--version", action="store_true", help="print the application version")
    launch_mode.add_argument(
        "--smoke-test",
        action="store_true",
        help="initialize the application and exit without entering the event loop",
    )
    return parser.parse_args(arguments)


def run_startup_smoke(application: QApplication, window: QWidget) -> int:
    """Exercise Qt and the fully composed main window, then exit immediately."""
    window.show()
    application.processEvents()
    window.close()
    application.processEvents()
    return 0


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
    if options.smoke_test:
        return run_startup_smoke(application, window)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
