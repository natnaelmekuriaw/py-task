from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from pytask_service.benchmarks.cases import plan_cases
from pytask_service.benchmarks.environment import Environment
from pytask_service.benchmarks.options import BenchmarkOptions
from pytask_service.benchmarks.results import CaseResult, RecoveryResult
from pytask_service.benchmarks.summary import summarize


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    options: BenchmarkOptions
    environment: Environment
    started_at: str
    results: tuple[CaseResult, ...] = ()
    recovery: RecoveryResult | None = None
    error_code: str | None = None
    finished_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def valid(self) -> bool:
        return (
            self.error_code is None
            and tuple(result.case for result in self.results)
            == plan_cases(self.options)
            and all(result.valid for result in self.results)
            and (
                not self.options.recovery or bool(self.recovery and self.recovery.valid)
            )
        )

    def document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "valid": self.valid,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "options": asdict(self.options),
            "environment": asdict(self.environment),
            "planned_cases": len(plan_cases(self.options)),
            "cases": [
                asdict(result) | {"valid": result.valid} for result in self.results
            ],
            "summary": [
                asdict(row) for row in summarize(self.results, self.options.repeat)
            ],
            "recovery": asdict(self.recovery) if self.recovery else None,
            "error_code": self.error_code,
            "measurement": {
                "scope": "host Python engine; configured PostgreSQL; no HTTP",
                "timing_unit": "seconds",
                "producer_transactions": "one per task",
                "elapsed_boundary": "first submission to observed batch completion",
                "percentile_method": "linear interpolation at (n - 1) * q",
                "worker_start_method": "spawn",
                "task_start_method": "spawn",
                "worker_pool_size": 1,
                "worker_max_overflow": 1,
                "worker_lease_seconds": 30,
                "worker_heartbeat_seconds": 10,
                "service_event_level": "WARNING",
            },
        }
