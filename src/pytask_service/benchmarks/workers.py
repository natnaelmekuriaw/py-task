import multiprocessing
from multiprocessing.context import BaseContext
from multiprocessing.process import BaseProcess
from types import TracebackType

from pytask_service.benchmarks.deadline import Deadline
from pytask_service.benchmarks.errors import BenchmarkFailure
from pytask_service.benchmarks.namespace import validate_schema_name
from pytask_service.benchmarks.worker import worker_main
from pytask_service.config import Settings
from pytask_service.worker.termination import stop_process


class WorkerGroup:
    def __init__(
        self,
        settings: Settings,
        schema: str,
        count: int,
        *,
        startup_timeout: float,
        shutdown_timeout: float,
        context: BaseContext | None = None,
    ) -> None:
        validate_schema_name(schema)
        if type(count) is not int or not 1 <= count <= 32:
            raise ValueError("Worker count must be from 1 to 32")
        self.settings, self.schema, self.count = settings, schema, count
        self.startup_timeout, self.shutdown_timeout = startup_timeout, shutdown_timeout
        self.context = context or multiprocessing.get_context("spawn")
        self.stopped = self.context.Event()
        self.processes: list[BaseProcess] = []

    def __enter__(self) -> "WorkerGroup":
        deadline = Deadline(self.startup_timeout)
        try:
            signals = []
            for index in range(self.count):
                deadline.check()
                ready = self.context.Event()
                process = self.context.Process(  # type: ignore[attr-defined]
                    target=worker_main,
                    args=(
                        self.settings,
                        self.schema,
                        self.stopped,
                        ready,
                        f"benchmark-{index}",
                    ),
                )
                try:
                    process.start()
                except BaseException:
                    process.close()
                    raise
                self.processes.append(process)
                signals.append(ready)
            for ready in signals:
                while not ready.is_set():
                    deadline.check()
                    self.ensure_alive()
                    ready.wait(min(0.05, deadline.remaining))
            deadline.check()
            self.ensure_alive()
            return self
        except BaseException:
            self.stop()
            raise

    def ensure_alive(self) -> None:
        if len(self.processes) != self.count or any(
            not process.is_alive() for process in self.processes
        ):
            raise BenchmarkFailure("worker_exited")

    def stop(self) -> bool:
        self.stopped.set()
        deadline = Deadline(self.shutdown_timeout)
        for process in self.processes:
            process.join(timeout=deadline.remaining)
        forced = False
        surviving = []
        for process in self.processes:
            if process.is_alive():
                forced = True
                try:
                    stop_process(process)
                except RuntimeError:
                    surviving.append(process)
                    continue
            process.close()
        self.processes = surviving
        if surviving:
            raise BenchmarkFailure("workers_still_running", retained_schema=self.schema)
        return forced

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        forced = self.stop()
        if forced and error is None:
            raise BenchmarkFailure("forced_worker_shutdown")
