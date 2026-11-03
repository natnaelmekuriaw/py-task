from unittest.mock import MagicMock

import pytest

from pytask_service.benchmarks import victim
from pytask_service.config import Settings

SCHEMA = "pytask_bench_" + "c" * 32


@pytest.fixture
def process_setup(monkeypatch):
    context, process, claimed = MagicMock(), MagicMock(), MagicMock()
    context.Event.return_value = claimed
    context.Process.return_value = process
    claimed.is_set.return_value = True
    process.is_alive.return_value = False
    process.exitcode = 7
    stop = MagicMock()
    monkeypatch.setattr(victim.multiprocessing, "get_context", lambda method: context)
    monkeypatch.setattr(victim, "stop_process", stop)
    return context, process, claimed, stop


def test_victim_must_exit_with_the_expected_crash_code(process_setup):
    context, process, _, stop = process_setup
    victim.crash_worker(Settings("unused"), SCHEMA, 1)
    process.start.assert_called_once()
    stop.assert_called_once_with(process)
    process.close.assert_called_once()
    assert context.Process.call_args.kwargs["target"] is victim.victim_main


def test_victim_exit_before_claim_is_a_failed_demonstration(process_setup):
    _, process, claimed, _ = process_setup
    claimed.is_set.return_value = False
    with pytest.raises(RuntimeError, match="victim_failed_before_claim"):
        victim.crash_worker(Settings("unused"), SCHEMA, 1)
    process.close.assert_called_once()


def test_unstoppable_victim_retains_its_schema(process_setup):
    _, process, _, stop = process_setup
    stop.side_effect = RuntimeError("cannot stop")
    with pytest.raises(RuntimeError, match="workers_still_running") as caught:
        victim.crash_worker(Settings("unused"), SCHEMA, 1)
    assert caught.value.retained_schema == SCHEMA
    process.close.assert_not_called()


@pytest.mark.parametrize(
    "error", [OSError("private process detail"), KeyboardInterrupt()]
)
def test_unexpected_victim_cleanup_failure_preserves_schema(process_setup, error):
    _, process, _, stop = process_setup
    stop.side_effect = error
    with pytest.raises(RuntimeError, match="worker_cleanup_failed") as caught:
        victim.crash_worker(Settings("unused"), SCHEMA, 1)
    assert caught.value.retained_schema == SCHEMA
    assert "private process detail" not in str(caught.value)
    process.close.assert_not_called()
