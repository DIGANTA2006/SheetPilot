from __future__ import annotations

from collections.abc import Sequence

import pytest
from pytest import MonkeyPatch

import sheetpilot.app.main as app_main


def test_smoke_test_argument_is_separate_from_normal_startup() -> None:
    options = app_main.parse_arguments(["--smoke-test"])

    assert options.smoke_test is True
    assert options.version is False


def test_version_and_smoke_test_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        app_main.parse_arguments(["--version", "--smoke-test"])


@pytest.mark.parametrize(
    "arguments",
    [
        ["--smoke-test", "--workflow-self-test"],
        ["--workflow-self-test", "--invalid-workflow-self-test"],
        ["--version", "--invalid-workflow-self-test"],
    ],
)
def test_all_self_test_modes_are_mutually_exclusive(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        app_main.parse_arguments(arguments)


@pytest.mark.parametrize(
    ("argument", "runner_name"),
    [
        ("--workflow-self-test", "run_normal_workflow_self_test"),
        ("--invalid-workflow-self-test", "run_invalid_workflow_self_test"),
    ],
)
def test_workflow_self_tests_run_without_ui_or_user_data_initialization(
    argument: str,
    runner_name: str,
    monkeypatch: MonkeyPatch,
) -> None:
    events: list[str] = []

    def run_self_test() -> None:
        events.append("self-test")

    def reject_ui_initialization() -> None:
        raise AssertionError(
            "workflow self-tests must not initialize the normal application context"
        )

    monkeypatch.setattr(app_main.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(app_main, runner_name, run_self_test)
    monkeypatch.setattr(app_main, "build_context", reject_ui_initialization)

    assert app_main.main([argument]) == 0
    assert events == ["self-test"]


def test_smoke_test_composes_window_without_entering_event_loop(
    monkeypatch: MonkeyPatch,
) -> None:
    events: list[str] = []
    context = object()

    class FakeApplication:
        _current: FakeApplication | None = None

        def __init__(self, arguments: Sequence[str]) -> None:
            assert list(arguments)
            type(self)._current = self
            events.append("application")

        @classmethod
        def instance(cls) -> FakeApplication | None:
            return cls._current

        def processEvents(self) -> None:  # noqa: N802 - mirrors the Qt API
            events.append("process-events")

        def exec(self) -> int:
            raise AssertionError("the smoke test must not enter the Qt event loop")

    class FakeWindow:
        def show(self) -> None:
            events.append("show")

        def close(self) -> None:
            events.append("close")

    def fake_build_context() -> object:
        events.append("context")
        return context

    def fake_build_main_window(
        application: FakeApplication, supplied_context: object
    ) -> FakeWindow:
        assert application is FakeApplication._current
        assert supplied_context is context
        events.append("window")
        return FakeWindow()

    monkeypatch.setattr(app_main, "QApplication", FakeApplication)
    monkeypatch.setattr(app_main.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(app_main, "build_context", fake_build_context)
    monkeypatch.setattr(app_main, "build_main_window", fake_build_main_window)

    assert app_main.main(["--smoke-test"]) == 0
    assert events == [
        "application",
        "context",
        "window",
        "show",
        "process-events",
        "close",
        "process-events",
    ]
