from unittest.mock import MagicMock

import pytest

from pytask_service.benchmarks.errors import BenchmarkFailure
from pytask_service.benchmarks.workers import WorkerGroup
from pytask_service.config import Settings

SCHEMA = "pytask_bench_" + "b" * 32


def group_with_process():
    group = WorkerGroup(
        Settings("unused"),
        SCHEMA,
        1,
        startup_timeout=1,
        shutdown_timeout=0,
        context=MagicMock(),
    )
    process = MagicMock()
    group.processes = [process]
    return group, process


def test_graceful_shutdown_does_not_terminate_workers(monkeypatch):
    stop = MagicMock()
    monkeypatch.setattr("pytask_service.benchmarks.workers.stop_process", stop)
    group, process = group_with_process()
    process.is_alive.return_value = False
    assert group.stop() is False
    assert group.processes == []
    stop.assert_not_called()
    process.close.assert_called_once()


def test_forced_shutdown_invalidates_an_otherwise_successful_case(monkeypatch):
    stop = MagicMock()
    monkeypatch.setattr("pytask_service.benchmarks.workers.stop_process", stop)
    group, process = group_with_process()
    process.is_alive.return_value = True
    with pytest.raises(BenchmarkFailure, match="forced_worker_shutdown"):
        group.__exit__(None, None, None)
    stop.assert_called_once_with(process)
    process.close.assert_called_once()


def test_unstoppable_worker_keeps_its_handle_and_schema(monkeypatch):
    stop = MagicMock(side_effect=RuntimeError("cannot stop"))
    monkeypatch.setattr("pytask_service.benchmarks.workers.stop_process", stop)
    group, process = group_with_process()
    process.is_alive.return_value = True
    with pytest.raises(BenchmarkFailure, match="workers_still_running") as caught:
        group.stop()
    assert caught.value.retained_schema == SCHEMA
    assert group.processes == [process]
    process.close.assert_not_called()


@pytest.mark.parametrize(
    "error", [OSError("private process detail"), KeyboardInterrupt()]
)
def test_interrupted_join_retains_schema_and_process_handle(error):
    group, process = group_with_process()
    process.join.side_effect = error
    with pytest.raises(BenchmarkFailure, match="worker_cleanup_failed") as caught:
        group.stop()
    assert caught.value.retained_schema == SCHEMA
    assert group.processes == [process]
    process.close.assert_not_called()
    assert "private process detail" not in str(caught.value)
