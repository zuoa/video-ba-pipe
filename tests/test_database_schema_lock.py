import peewee as pw
import pytest

from app import setup_database as database_setup


class _BooleanCursor:
    def __init__(self, value):
        self.value = value
        self.closed = False

    def fetchone(self):
        return (self.value,)

    def close(self):
        self.closed = True


def test_postgres_schema_lock_waits_and_releases(monkeypatch):
    postgres_db = pw.PostgresqlDatabase('schema-lock-test')
    responses = iter((False, True, True))
    queries = []
    sleeps = []

    def execute_sql(sql, params):
        cursor = _BooleanCursor(next(responses))
        queries.append((sql, params, cursor))
        return cursor

    monkeypatch.setattr(database_setup, 'db', postgres_db)
    monkeypatch.setattr(postgres_db, 'execute_sql', execute_sql)
    monkeypatch.setattr(database_setup.time, 'sleep', sleeps.append)

    with database_setup._database_schema_lock():
        queries.append(('inside-critical-section', (), None))

    assert [query[0] for query in queries] == [
        'SELECT pg_try_advisory_lock(%s)',
        'SELECT pg_try_advisory_lock(%s)',
        'inside-critical-section',
        'SELECT pg_advisory_unlock(%s)',
    ]
    assert sleeps == [database_setup._SCHEMA_LOCK_POLL_SECONDS]
    assert all(cursor.closed for _, _, cursor in queries if cursor is not None)


def test_schema_lock_timeout_configuration_rejects_invalid_value(monkeypatch):
    monkeypatch.setenv('DB_SCHEMA_LOCK_TIMEOUT_SECONDS', 'not-a-number')

    with pytest.raises(ValueError, match='must be a number'):
        database_setup._schema_lock_timeout_seconds()


def test_portability_columns_are_added_before_peewee_creates_indexes(monkeypatch):
    events = []

    monkeypatch.setattr(database_setup.db, 'table_exists', lambda _table: False)
    monkeypatch.setattr(
        database_setup,
        '_ensure_portability_columns',
        lambda *, create_indexes=True: events.append(
            ('portability', create_indexes)
        ),
    )
    monkeypatch.setattr(
        database_setup.db,
        'create_tables',
        lambda _models, safe: events.append(('create_tables', safe)),
    )
    for helper_name in (
        '_ensure_ownership_columns',
        '_ensure_video_source_columns',
        '_ensure_model_columns',
        '_ensure_workflow_columns',
        '_normalize_existing_records',
        '_ensure_workflow_indexes',
        'ensure_default_admin_user',
    ):
        monkeypatch.setattr(database_setup, helper_name, lambda: None)

    database_setup._apply_schema_changes()

    assert events == [
        ('portability', False),
        ('create_tables', True),
        ('portability', True),
    ]
