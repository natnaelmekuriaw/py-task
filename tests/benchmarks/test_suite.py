from dataclasses import replace
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from pytask_service.benchmarks import suite
from pytask_service.benchmarks.errors import BenchmarkFailure
from pytask_service.benchmarks.results import CaseResult
from pytask_service.config import Settings


@pytest.fixture
def dependencies(monkeypatch, metrics):
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.scalar.return_value = "17.0"
    monkeypatch.setattr(suite, "build_engine", lambda _: engine)
    monkeypatch.setattr(suite, "source_revision", lambda: (None, None))
    run = MagicMock(
        side_effect=lambda admin, settings, case, options: CaseResult(case, metrics)
    )
    recovery = MagicMock()
    monkeypatch.setattr(suite, "run_case", run)
    monkeypatch.setattr(suite, "run_recovery", recovery)
    return engine, run, recovery


def test_successful_suite_preserves_case_order_and_disposes_engine(
    options, dependencies
):
    engine, run, recovery = dependencies
    messages = []
    report = suite.run_suite(Settings("unused"), options, progress=messages.append)
    assert report.valid
    assert report.environment.postgres == "17.0"
    assert datetime.fromisoformat(report.finished_at) >= datetime.fromisoformat(
        report.started_at
    )
    assert len(messages) == run.call_count == 1
    recovery.assert_not_called()
    engine.dispose.assert_called_once()


def test_failed_case_stops_remaining_work_and_keeps_partial_report(
    options, dependencies
):
    engine, run, recovery = dependencies
    run.side_effect = BenchmarkFailure(
        "workers_still_running", retained_schema="pytask_bench_" + "a" * 32
    )
    report = suite.run_suite(
        Settings("unused"),
        replace(options, workers=(1, 2), recovery=True),
        progress=lambda _: None,
    )
    assert not report.valid
    assert report.error_code == "case_failed"
    assert report.results[0].error_code == "workers_still_running"
    assert report.results[0].retained_schema
    assert run.call_count == 1
    recovery.assert_not_called()
    engine.dispose.assert_called_once()


def test_cancellation_is_recorded_without_success_metrics(options, dependencies):
    _, run, _ = dependencies
    run.side_effect = KeyboardInterrupt
    report = suite.run_suite(Settings("unused"), options, progress=lambda _: None)
    assert report.error_code == "interrupted"
    assert report.results[0].metrics is None


def test_engine_initialization_error_returns_a_safe_report(
    monkeypatch, options, dependencies
):
    def fail(_):
        raise RuntimeError("postgresql://private:secret@host/db")

    monkeypatch.setattr(suite, "build_engine", fail)
    report = suite.run_suite(Settings("unused"), options, progress=lambda _: None)
    assert report.error_code == "unexpected_error"
    assert "secret" not in str(report.document())
