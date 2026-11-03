import json
import sys
from collections.abc import Sequence
from tempfile import TemporaryFile

from pytask_service.benchmarks.arguments import parser, read_options
from pytask_service.benchmarks.artifacts import write_reports
from pytask_service.benchmarks.cases import plan_cases
from pytask_service.benchmarks.suite import run_suite
from pytask_service.config import Settings


def main(argv: Sequence[str] | None = None) -> int:
    command = parser()
    args = command.parse_args(argv)
    try:
        options = read_options(args)
        settings = Settings.from_env()
    except ValueError as error:
        print(f"Benchmark configuration failed: {error}", file=sys.stderr)
        return 2
    try:
        args.output.mkdir(parents=True, exist_ok=True)
        with TemporaryFile(dir=args.output) as probe:
            probe.write(b"pytask")
            probe.flush()
    except OSError:
        print(
            "Benchmark output directory is unavailable or not writable.",
            file=sys.stderr,
        )
        return 1
    cases = len(plan_cases(options))
    recovery = "enabled" if options.recovery else "disabled"
    print(
        f"Benchmark plan: {cases} cases, {cases * options.tasks} measured tasks, "
        f"{cases * options.warmup} warmup tasks; recovery {recovery}.",
        file=sys.stderr,
        flush=True,
    )
    report = run_suite(
        settings,
        options,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    try:
        output = write_reports(report, args.output)
    except (OSError, ValueError):
        print(
            "Cannot save benchmark reports; check output directory access.",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "valid": report.valid,
                "attempted_cases": len(report.results),
                "completed_cases": sum(result.valid for result in report.results),
                "report_directory": str(output),
                "error_code": report.error_code,
            }
        )
    )
    if report.error_code == "interrupted":
        return 130
    return 0 if report.valid else 1
