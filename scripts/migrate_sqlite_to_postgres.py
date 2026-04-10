#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Iterable

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault('DB_BACKEND', 'postgres')

try:
    import peewee as pw

    from app.core.database_models import (  # noqa: E402
        Algorithm,
        AlgorithmHook,
        Alert,
        Hook,
        MLModel,
        ScriptExecutionLog,
        ScriptVersion,
        SourceHealthLog,
        SystemSetting,
        User,
        VideoSource,
        Workflow,
        WorkflowConnection,
        WorkflowNode,
        WorkflowTestResult,
        db,
    )
    from app.setup_database import ensure_default_admin_user, setup_database  # noqa: E402
except ModuleNotFoundError as exc:  # pragma: no cover - depends on local env
    pw = None
    db = None
    User = None
    MODELS = []
    MODULE_IMPORT_ERROR = exc
else:
    MODULE_IMPORT_ERROR = None
    MODELS = [
        Algorithm,
        VideoSource,
        Workflow,
        WorkflowNode,
        WorkflowConnection,
        Hook,
        AlgorithmHook,
        ScriptVersion,
        ScriptExecutionLog,
        MLModel,
        User,
        Alert,
        WorkflowTestResult,
        SourceHealthLog,
        SystemSetting,
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Migrate data from a legacy SQLite database into PostgreSQL.',
    )
    parser.add_argument(
        '--sqlite-path',
        required=True,
        help='Path to the source SQLite database file.',
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=500,
        help='Number of rows to insert per batch.',
    )
    parser.add_argument(
        '--truncate-first',
        action='store_true',
        help='Truncate PostgreSQL tables before importing data.',
    )
    return parser.parse_args()


def sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def sqlite_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return {row[1] for row in rows}


def chunked_rows(
    conn: sqlite3.Connection,
    table_name: str,
    select_columns: list[str],
    batch_size: int,
) -> Iterable[list[sqlite3.Row]]:
    query = (
        f'SELECT {", ".join(f"""\"{column}\"""" for column in select_columns)} '
        f'FROM "{table_name}"'
    )
    cursor = conn.execute(query)

    batch: list[sqlite3.Row] = []
    for row in cursor:
        batch.append(row)
        if len(batch) >= batch_size:
            yield batch
            batch = []

    if batch:
        yield batch


def truncate_destination() -> None:
    for model in reversed(MODELS):
        db.execute_sql(
            f'TRUNCATE TABLE "{model._meta.table_name}" RESTART IDENTITY CASCADE;'
        )


def ensure_destination_empty() -> None:
    non_empty = []
    for model in MODELS:
        if model.select().limit(1).exists():
            non_empty.append(model._meta.table_name)

    if non_empty:
        joined = ', '.join(non_empty)
        raise RuntimeError(
            'Destination PostgreSQL database is not empty. '
            f'Use --truncate-first to clear existing tables: {joined}'
        )


def destination_has_only_bootstrap_data() -> bool:
    for model in MODELS:
        rows = list(model.select().limit(2))
        if model is User:
            if not rows:
                continue
            if len(rows) != 1:
                return False
            user = rows[0]
            if user.username != 'admin' or user.role != 'admin':
                return False
            continue
        if rows:
            return False
    return True


def migrate_model(conn: sqlite3.Connection, model: type[pw.Model], batch_size: int) -> int:
    table_name = model._meta.table_name
    if not sqlite_table_exists(conn, table_name):
        print(f'[skip] {table_name}: source table does not exist')
        return 0

    available_columns = sqlite_columns(conn, table_name)
    field_pairs = [
        (field.name, field.column_name)
        for field in model._meta.sorted_fields
        if field.column_name in available_columns
    ]

    if not field_pairs:
        print(f'[skip] {table_name}: no overlapping columns')
        return 0

    selected_columns = [column_name for _, column_name in field_pairs]
    inserted = 0

    for rows in chunked_rows(conn, table_name, selected_columns, batch_size):
        payload = []
        for row in rows:
            item = {}
            for field_name, column_name in field_pairs:
                item[field_name] = row[column_name]
            payload.append(item)

        with db.atomic():
            model.insert_many(payload).execute()
        inserted += len(payload)

    print(f'[ok] {table_name}: imported {inserted} rows')
    return inserted


def sync_sequence(model: type[pw.Model]) -> None:
    primary_key = model._meta.primary_key
    if not isinstance(primary_key, pw.AutoField):
        return

    table_name = model._meta.table_name
    pk_column = primary_key.column_name
    db.execute_sql(
        f"""
        SELECT setval(
            pg_get_serial_sequence('{table_name}', '{pk_column}'),
            COALESCE(MAX("{pk_column}"), 1),
            MAX("{pk_column}") IS NOT NULL
        )
        FROM "{table_name}";
        """
    )


def main() -> int:
    args = parse_args()
    if MODULE_IMPORT_ERROR is not None:
        print(
            'Missing Python dependencies for migration script. '
            'Install project requirements first.',
            file=sys.stderr,
        )
        print(f'Original error: {MODULE_IMPORT_ERROR}', file=sys.stderr)
        return 2

    sqlite_path = os.path.abspath(args.sqlite_path)
    if not os.path.exists(sqlite_path):
        print(f'SQLite database not found: {sqlite_path}', file=sys.stderr)
        return 1

    setup_database()
    db.connect(reuse_if_open=True)

    try:
        if args.truncate_first:
            truncate_destination()
        elif destination_has_only_bootstrap_data():
            truncate_destination()
        else:
            ensure_destination_empty()

        sqlite_conn = sqlite3.connect(sqlite_path)
        sqlite_conn.row_factory = sqlite3.Row
        try:
            total_rows = 0
            for model in MODELS:
                total_rows += migrate_model(sqlite_conn, model, args.batch_size)
        finally:
            sqlite_conn.close()

        ensure_default_admin_user()
        for model in MODELS:
            sync_sequence(model)

        print(f'Migration completed successfully. Total imported rows: {total_rows}')
        return 0
    finally:
        if not db.is_closed():
            db.close()


if __name__ == '__main__':
    raise SystemExit(main())
