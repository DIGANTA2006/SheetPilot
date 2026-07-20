"""SheetPilot desktop entry point."""

from __future__ import annotations

import argparse
import multiprocessing
import sys
from collections.abc import Sequence
from typing import Any

from sheetpilot.app.workflow_self_test import (
    run_invalid_workflow_self_test,
    run_normal_workflow_self_test,
)

QApplication: Any = None
build_context: Any = None
build_main_window: Any = None


def _load_ui_dependencies() -> None:
    """Import Qt only for modes that actually create a desktop window."""
    global QApplication, build_context, build_main_window
    if QApplication is None:
        from PySide6.QtWidgets import QApplication as QtApplication

        QApplication = QtApplication
    if build_context is None or build_main_window is None:
        from sheetpilot.app.bootstrap import build_context as context_builder
        from sheetpilot.app.bootstrap import build_main_window as window_builder

        build_context = context_builder
        build_main_window = window_builder


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SheetPilot desktop application")
    launch_mode = parser.add_mutually_exclusive_group()
    launch_mode.add_argument("--version", action="store_true", help="print the application version")
    launch_mode.add_argument(
        "--smoke-test",
        action="store_true",
        help="initialize the application and exit without entering the event loop",
    )
    launch_mode.add_argument(
        "--workflow-self-test",
        action="store_true",
        help="run a disposable normal XLSX workflow and exit",
    )
    launch_mode.add_argument(
        "--invalid-workflow-self-test",
        action="store_true",
        help="prove an unknown workflow is rejected without artifacts and exit",
    )
    return parser.parse_args(arguments)


def run_startup_smoke(application: Any, window: Any) -> int:
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
    if options.workflow_self_test:
        run_normal_workflow_self_test()
        return 0
    if options.invalid_workflow_self_test:
        run_invalid_workflow_self_test()
        return 0
    _load_ui_dependencies()
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = QApplication(sys.argv[:1])
    context = build_context()
    window = build_main_window(application, context)
    if options.smoke_test:
        return run_startup_smoke(application, window)
    window.show()
    return int(application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
