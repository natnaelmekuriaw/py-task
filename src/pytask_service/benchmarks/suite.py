from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from pytask_service.benchmarks.cases import plan_cases
from pytask_service.benchmarks.environment import host_environment
from pytask_service.benchmarks.errors import BenchmarkFailure
from pytask_service.benchmarks.execution import run_case
from pytask_service.benchmarks.options import BenchmarkOptions
from pytask_service.benchmarks.recovery import run_recovery
from pytask_service.benchmarks.report import BenchmarkReport
from pytask_service.benchmarks.results import CaseResult, RecoveryResult
from pytask_service.benchmarks.revision import source_revision
from pytask_service.config import Settings
from pytask_service.db.engine import build_engine


def failure_code(error: Exception) -> str:
    if isinstance(error, BenchmarkFailure):
        return error.code
    return (
        "database_error" if isinstance(error, SQLAlchemyError) else "unexpected_error"
    )


def run_suite(
    settings: Settings,
    options: BenchmarkOptions,
    *,
    progress: Callable[[str], None],
) -> BenchmarkReport:
    report = _run_suite(settings, options, progress=progress)
    return replace(report, finished_at=datetime.now(UTC).isoformat())


def _run_suite(
    settings: Settings,
    options: BenchmarkOptions,
    *,
    progress: Callable[[str], None],
) -> BenchmarkReport:
    revision, dirty = source_revision()
    environment = replace(host_environment(), revision=revision, tracked_changes=dirty)
    report = BenchmarkReport(options, environment, datetime.now(UTC).isoformat())
    engine = None
    results: list[CaseResult] = []
    try:
        engine = build_engine(settings)
        with engine.connect() as connection:
            version = connection.scalar(text("SHOW server_version"))
        report = replace(
            report, environment=replace(environment, postgres=str(version))
        )
        cases = plan_cases(options)
        for index, case in enumerate(cases, 1):
            progress(
                f"Case {index}/{len(cases)}: {case.workload.value}, "
                f"{case.workers} workers, repetition {case.repetition}/{options.repeat}"
            )
            try:
                results.append(run_case(engine, settings, case, options))
            except KeyboardInterrupt:
                results.append(CaseResult(case, error_code="interrupted"))
                return replace(report, results=tuple(results), error_code="interrupted")
            except Exception as error:
                retained = (
                    error.retained_schema
                    if isinstance(error, BenchmarkFailure)
                    else None
                )
                results.append(
                    CaseResult(
                        case, error_code=failure_code(error), retained_schema=retained
                    )
                )
                return replace(report, results=tuple(results), error_code="case_failed")
        report = replace(report, results=tuple(results))
        if options.recovery:
            progress("Recovery: worker crash followed by natural lease expiry")
            try:
                report = replace(
                    report, recovery=run_recovery(engine, settings, options)
                )
            except Exception as error:
                retained = (
                    error.retained_schema
                    if isinstance(error, BenchmarkFailure)
                    else None
                )
                report = replace(
                    report,
                    recovery=RecoveryResult(
                        False,
                        error_code=failure_code(error),
                        retained_schema=retained,
                    ),
                    error_code="recovery_failed",
                )
        return report
    except KeyboardInterrupt:
        return replace(report, results=tuple(results), error_code="interrupted")
    except Exception as error:
        return replace(report, results=tuple(results), error_code=failure_code(error))
    finally:
        if engine is not None:
            engine.dispose()
