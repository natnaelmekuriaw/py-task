from types import TracebackType

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateSchema, DropSchema

from pytask_service.benchmarks.engine import check_scope, scoped_engine
from pytask_service.benchmarks.errors import BenchmarkFailure
from pytask_service.benchmarks.namespace import new_schema_name
from pytask_service.config import Settings
from pytask_service.db.models import Task


class BenchmarkDatabase:
    def __init__(self, admin: Engine, settings: Settings) -> None:
        self.admin = admin
        self.settings = settings
        self.schema = new_schema_name()
        self.engine: Engine | None = None
        self._created = False

    def __enter__(self) -> "BenchmarkDatabase":
        try:
            with self.admin.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                connection.execute(text("SET LOCAL statement_timeout = '5s'"))
                connection.execute(CreateSchema(self.schema))
                self._created = True
            self.engine = scoped_engine(self.settings, self.schema)
            check_scope(self.engine, self.schema)
            Task.metadata.create_all(self.engine)
            return self
        except BaseException:
            self.close()
            raise

    def close(self, *, retain: bool = False) -> None:
        if self.engine is not None:
            try:
                self.engine.dispose()
            except BaseException:
                raise BenchmarkFailure(
                    "schema_cleanup_failed",
                    retained_schema=self.schema if self._created else None,
                ) from None
        if self._created and not retain:
            try:
                with self.admin.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                    connection.execute(text("SET LOCAL statement_timeout = '5s'"))
                    connection.execute(
                        DropSchema(self.schema, cascade=True, if_exists=True)
                    )
            except Exception:
                raise BenchmarkFailure(
                    "schema_cleanup_failed", retained_schema=self.schema
                ) from None
            self._created = False

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        retain = (
            isinstance(error, BenchmarkFailure) and error.retained_schema == self.schema
        )
        self.close(retain=retain)

    def connected_engine(self) -> Engine:
        if self.engine is None or not self._created:
            raise BenchmarkFailure("schema_not_initialized")
        return self.engine
