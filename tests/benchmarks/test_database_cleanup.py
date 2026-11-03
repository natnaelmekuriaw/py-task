from unittest.mock import MagicMock

import pytest

from pytask_service.benchmarks.database import BenchmarkDatabase
from pytask_service.benchmarks.errors import BenchmarkFailure
from pytask_service.config import Settings


def test_uninitialized_schema_cannot_supply_an_engine():
    with pytest.raises(BenchmarkFailure, match="schema_not_initialized"):
        BenchmarkDatabase(MagicMock(), Settings("unused")).connected_engine()


def test_unreaped_workers_retain_only_their_owned_schema():
    admin, engine = MagicMock(), MagicMock()
    database = BenchmarkDatabase(admin, Settings("unused"))
    database.engine, database._created = engine, True
    error = BenchmarkFailure("workers_still_running", retained_schema=database.schema)
    database.__exit__(type(error), error, None)
    admin.begin.assert_not_called()
    engine.dispose.assert_called_once()


def test_cleanup_failure_reports_a_safe_schema_identifier():
    admin = MagicMock()
    admin.begin.side_effect = RuntimeError("private connection details")
    database = BenchmarkDatabase(admin, Settings("unused"))
    database._created = True
    with pytest.raises(BenchmarkFailure, match="schema_cleanup_failed") as caught:
        database.close()
    assert caught.value.retained_schema == database.schema
    assert "private connection details" not in str(caught.value)


def test_successful_cleanup_is_idempotent():
    admin = MagicMock()
    database = BenchmarkDatabase(admin, Settings("unused"))
    database._created = True
    database.close()
    database.close()
    admin.begin.assert_called_once()


@pytest.mark.parametrize("error", [OSError("private detail"), KeyboardInterrupt()])
def test_dispose_failure_retains_schema_without_attempting_ddl(error):
    admin, engine = MagicMock(), MagicMock()
    database = BenchmarkDatabase(admin, Settings("unused"))
    database.engine, database._created = engine, True
    engine.dispose.side_effect = error
    with pytest.raises(BenchmarkFailure, match="schema_cleanup_failed") as caught:
        database.close()
    assert caught.value.retained_schema == database.schema
    admin.begin.assert_not_called()
    assert "private detail" not in str(caught.value)
