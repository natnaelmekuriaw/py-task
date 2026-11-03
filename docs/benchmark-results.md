# Benchmark reports

Every invocation writes a unique UTC timestamp and random identifier
directory containing `results.json`, `results.csv` and `results.md`.
Files use exclusive creation and never replace an earlier run.
The command returns nonzero for failed, interrupted or unsaved reports.
Output access is checked before benchmark execution. If a later file write
fails, the writer removes the files it created for that run. A cleanup failure
can still leave incomplete artifacts; an unsuccessful command is never a
complete report set.

## JSON contract

Schema version 1 includes:

- `valid`, start and finish timestamps, planned case count and a safe error code.
- The exact options, including workload cost and timeout settings.
- Python implementation and version, platform, logical CPU count, installed
  package versions, PostgreSQL version and Git revision when available.
- Whether tracked source differs from that revision; untracked files are
  not included in this flag.
- Every attempted case, its workload, worker count, repetition and validity.
- Metrics for valid cases, summaries for complete repetition groups, and a
  separate recovery result.
- Timing boundaries, percentile method, process start method, pool settings,
  lease settings and service event log level.

The finish timestamp is captured after suite cleanup and stays the same when
the report is rendered again. The command's final stdout summary distinguishes
`attempted_cases` from successfully `completed_cases`; a failed case only
increments the former.

The report excludes database URLs, API tokens, task arguments, task results,
hostnames, SQL and raw exception messages. Review platform and revision
metadata before sharing a report publicly.

## Metrics

| Field | Definition |
| --- | --- |
| Throughput | Succeeded tasks / monotonic batch elapsed seconds |
| Submission rate | Submitted tasks / submission-loop seconds |
| Latency | Task finish minus creation time |
| Queue wait | First attempt start minus eligibility time |
| Attempt duration | Attempt finish minus start time, including handler process overhead |
| Distribution | Sample count, min, arithmetic mean, p50, p95, p99 and max |

JSON timings are seconds. CSV duration headings explicitly use seconds or
milliseconds. Markdown displays tasks per second and latency p95 in
milliseconds. Percentiles use linear interpolation at `(n - 1) * q`;
small samples give very limited evidence about tail latency.

A baseline case must have exactly one successful attempt for every submitted
task, with matching results. Failed or incomplete cases have no metrics.
The coordinator stops at the first failed case and saves the partial run.

A scenario summary requires every expected repetition exactly once and all
of them valid. It uses median throughput and shows the full min/max range.
Speedup divides that median by the same workload's complete one-worker
median. Missing or failed baselines produce no speedup.

## Comparing runs

Keep task counts, workload cost, repetitions, Python dependencies, host
resources, PostgreSQL placement and background load consistent. Record
Docker resource limits or remote database latency alongside published
results; automatic platform metadata cannot capture all deployment details.

Compare repetition ranges as well as medians. Worker counts rotate in order
across repetitions, but workload order stays fixed. There is no randomization
or statistical significance calculation. Results from Windows host processes
and Linux containers describe different execution environments.

Archive all three files together. A missing artifact or failed command needs
investigation even when an earlier JSON file contains successful cases.
Use [troubleshooting](benchmark-troubleshooting.md) for safe error codes.
