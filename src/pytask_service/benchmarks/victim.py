import multiprocessing
import os
import signal
from multiprocessing.synchronize import Event
from threading import Event as ThreadEvent

from pytask_service.benchmarks.deadline import Deadline
from pytask_service.benchmarks.engine import check_scope, scoped_engine
from pytask_service.benchmarks.errors import BenchmarkFailure
from pytask_service.benchmarks.worker import REGISTRY_PATH
from pytask_service.config import Settings
from pytask_service.worker.claim import ClaimedTask
from pytask_service.worker.outcome import TaskOutcome
from pytask_service.worker.policy import WorkerPolicy
from pytask_service.worker.service import Worker
from pytask_service.worker.termination import stop_process


def victim_main(settings: Settings, schema: str, claimed: Event) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    engine = scoped_engine(settings, schema)

    def crash(registry: str, task: ClaimedTask, cancelled: ThreadEvent) -> TaskOutcome:
        claimed.set()
        os._exit(7)

    try:
        check_scope(engine, schema)
        Worker(
            engine,
            REGISTRY_PATH,
            worker_id="benchmark-victim",
            executor=crash,
            policy=WorkerPolicy(lease_seconds=3, heartbeat_seconds=0.5),
        ).run_once()
    except Exception:
        raise SystemExit(1) from None
    finally:
        engine.dispose()


def crash_worker(settings: Settings, schema: str, timeout: float) -> None:
    context = multiprocessing.get_context("spawn")
    claimed = context.Event()
    process = context.Process(
        target=victim_main,
        args=(settings, schema, claimed),
    )
    started = False
    deadline = Deadline(timeout)
    try:
        process.start()
        started = True
        while not claimed.is_set():
            deadline.check()
            if not process.is_alive():
                raise BenchmarkFailure("victim_failed_before_claim")
            claimed.wait(min(0.05, deadline.remaining))
        process.join(timeout=deadline.remaining)
        if process.is_alive() or process.exitcode != 7:
            raise BenchmarkFailure("victim_did_not_crash")
    finally:
        if started:
            try:
                stop_process(process)
            except RuntimeError:
                raise BenchmarkFailure(
                    "workers_still_running", retained_schema=schema
                ) from None
            except BaseException:
                raise BenchmarkFailure(
                    "worker_cleanup_failed", retained_schema=schema
                ) from None
        process.close()
