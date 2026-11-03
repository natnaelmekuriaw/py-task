from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pytask_service.benchmarks.csv_report import render_csv
from pytask_service.benchmarks.json_report import render_json
from pytask_service.benchmarks.markdown_report import render_markdown
from pytask_service.benchmarks.report import BenchmarkReport


def _write_new(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        try:
            stream.write(content)
        except BaseException:
            stream.close()
            path.unlink(missing_ok=True)
            raise


def write_reports(report: BenchmarkReport, directory: Path) -> Path:
    documents = (
        ("results.json", render_json(report)),
        ("results.csv", render_csv(report)),
        ("results.md", render_markdown(report)),
    )
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex
    output = directory / name
    output.mkdir(parents=True, exist_ok=False)
    written: list[Path] = []
    try:
        for filename, content in documents:
            path = output / filename
            _write_new(path, content)
            written.append(path)
    except BaseException:
        for path in written:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            output.rmdir()
        except OSError:
            pass
        raise
    return output
