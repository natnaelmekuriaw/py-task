from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pytask_service.benchmarks.workers import WorkerGroup
from pytask_service.config import Settings

SCHEMA = "pytask_bench_" + "a" * 32


def make_group(count=1):
    context = MagicMock()
    processes = []

    def new_process(**kwargs):
        process = MagicMock()
        process.arguments = kwargs
        process.is_alive.return_value = True
        process.join.side_effect = lambda **_: setattr(
            process.is_alive, "return_value", False
        )
        processes.append(process)
        return process

    context.Process.side_effect = new_process
    context.Event.side_effect = lambda: SimpleNamespace(
        set=MagicMock(), is_set=lambda: True, wait=MagicMock()
    )
    group = WorkerGroup(
        Settings("unused"),
        SCHEMA,
        count,
        startup_timeout=1,
        shutdown_timeout=1,
        context=context,
    )
    return group, context, processes


def test_ready_workers_use_only_owned_spawn_targets():
    group, context, processes = make_group(2)
    with group:
        assert len(processes) == 2
        assert all(process.start.call_count == 1 for process in processes)
        assert all(process.arguments["args"][1] == SCHEMA for process in processes)
    assert all(process.close.call_count == 1 for process in processes)


def test_partial_startup_failure_closes_both_process_handles():
    group, context, processes = make_group(2)
    original = context.Process.side_effect

    def process_factory(**kwargs):
        process = original(**kwargs)
        if len(processes) == 2:
            process.start.side_effect = OSError("start failed")
        return process

    context.Process.side_effect = process_factory
    with pytest.raises(OSError):
        group.__enter__()
    assert len(processes) == 2
    assert all(process.close.call_count == 1 for process in processes)
    processes[0].join.assert_called_once()
    processes[1].join.assert_not_called()


def test_empty_or_dead_worker_groups_fail_liveness_checks():
    group, _, processes = make_group()
    with pytest.raises(RuntimeError, match="worker_exited"):
        group.ensure_alive()
    group.__enter__()
    processes[0].is_alive.return_value = False
    with pytest.raises(RuntimeError, match="worker_exited"):
        group.ensure_alive()
    group.stop()


def test_startup_budget_is_checked_even_when_ready_is_already_set(monkeypatch):
    from pytask_service.benchmarks.deadline import Deadline

    now = [0.0]
    monkeypatch.setattr(
        "pytask_service.benchmarks.workers.Deadline",
        lambda seconds: Deadline(seconds, clock=lambda: now[0]),
    )
    group, context, processes = make_group()
    original = context.Process.side_effect

    def factory(**kwargs):
        process = original(**kwargs)
        process.start.side_effect = lambda: now.__setitem__(0, 2.0)
        return process

    context.Process.side_effect = factory
    with pytest.raises(RuntimeError, match="deadline_exceeded"):
        group.__enter__()
    processes[0].close.assert_called_once()
