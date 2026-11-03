# Benchmark troubleshooting

The verification script stops at the first failure. Read the last named
step before rerunning it. API and worker containers can remain stopped
after a failed check; successful completion leaves them running.

Invalid worker selections, oversized task plans and blank output paths are
rejected before the verification script runs any Docker commands. The benchmark
module also checks output-directory access before starting the measured suite.

## Slow runs

The command includes image building, migrations, all tests and hundreds of
fresh Python task processes. Benchmark progress shows the workload, worker
count and repetition. The default benchmark has 540 measured tasks; it is
not a single in-process function timing.

After an unchanged installation and image, `-SkipInstall -SkipBuild` avoids
those two setup steps. Use fewer tasks or repetitions for a functional smoke
check, then restore repeated measurements for performance comparisons.

A higher worker count can be slower due to process startup, database
contention, CPU scheduling and limited Docker resources. Small changes
within the observed repetition range do not establish an improvement.

## Error codes

| Code | What to inspect |
| --- | --- |
| `database_error` | Reachability, role permissions, connection limits, PostgreSQL logs |
| `schema_isolation_failed` | Schema creation and the exact connection search path |
| `deadline_exceeded` | Host load, per-case budget and worker failures |
| `worker_exited` | Worker stderr and database availability |
| `task_failed` | Handler timeout, execution environment and worker stderr |
| `unexpected_task_result` | Handler/reference mismatch; do not publish the case |
| `invalid_attempt_history` | Unexpected retry, duplicate observation or missing attempt |
| `forced_worker_shutdown` | A worker exceeded the graceful shutdown budget |
| `workers_still_running` | A process could not be reaped; its schema is retained |
| `worker_cleanup_failed` | Process cleanup failed or was interrupted; its schema is retained |
| `schema_cleanup_failed` | Permissions, locks or lost database connectivity |
| `victim_failed_before_claim` | Victim startup or database claim failure |
| `victim_did_not_crash` | Missing expected exit code 7 or a victim still running |
| `interrupted` | Operator cancellation; throughput summaries may be incomplete |
| `unexpected_error` | An unclassified local failure; narrow it with a tiny direct run |

The command keeps raw SQL exceptions and credentials out of reports. Use
local PostgreSQL diagnostics for connection and lock problems, and redact
them before sharing.

## Retained schemas

The report gives an exact generated name starting with `pytask_bench_`.
Confirm that the related benchmark processes are gone and that the schema
belongs to the failed run before asking a database administrator to remove
that exact schema. Existing application schemas are not cleanup targets.

If a machine restart or forceful command termination produced no report,
inspect schema creation and local processes manually. The suite has no
automatic sweeping of schemas from previous invocations.

## Missing reports

Check the final `report_directory` output and write permissions. Output
directory failures return exit 1. Partial artifacts can remain after a
filesystem failure; a complete set and a zero exit code are both required.
The default output directory is intentionally ignored by Git.

Configuration errors return 2 before database work. A recorded interrupt
returns 130. All other unsuccessful benchmark outcomes return 1. The
verification script converts a failed step into its own exit code 1.

See [configuration](benchmark-configuration.md) and
[report definitions](benchmark-results.md) before changing timeout budgets.
