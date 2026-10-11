"""Isolate regression tests from non-replayable legacy migration history."""

from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test.runner import DiscoverRunner


class SchemaBaselineTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        result = super().setup_databases(**kwargs)
        connection = connections["default"]
        if not connection.settings_dict["NAME"].startswith("test_toto_color_"):
            raise RuntimeError("Schema baseline setup is restricted to the temporary test database.")
        # TEST.MIGRATE=False creates the current schema in this new database.
        # Record its state so migration regression tests can exercise the real
        # 0034 -> 0035 transition without replaying unrelated legacy SQL.
        executor = MigrationExecutor(connection)
        recorder = MigrationRecorder(connection)
        for app_label, migration_name in executor.loader.graph.nodes:
            recorder.record_applied(app_label, migration_name)
        return result
