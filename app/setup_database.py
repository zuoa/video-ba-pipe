from contextlib import contextmanager
import logging
import os
import time

import peewee as pw

from app.core.database_models import (
    db, Algorithm, VideoSource, Alert, AlertDeliveryTask, AlertExportTask,
    ScriptVersion, Hook, AlgorithmHook, ScriptExecutionLog, MLModel,
    Workflow, WorkflowNode, WorkflowConnection, WorkflowTestResult, User, ApiKey, SourceHealthLog,
    SystemSetting, ExternalApi, FaceModelBundle, FaceModelArtifact, FaceGallery,
    FacePerson, FaceGalleryMembership, FaceTemplate, FaceEvent, FaceImportJob,
)


logger = logging.getLogger(__name__)

# Stable, application-specific PostgreSQL advisory-lock key ("VBPIPEDB").
# A session lock is used so every DDL/DML statement in the schema bootstrap is
# protected even if a helper opens a nested transaction.
_SCHEMA_LOCK_ID = 0x5642504950454442
_SCHEMA_LOCK_POLL_SECONDS = 0.25

_DATABASE_MODELS = (
    Algorithm,
    VideoSource,
    ExternalApi,
    Alert,
    AlertDeliveryTask,
    AlertExportTask,
    ScriptVersion,
    Hook,
    AlgorithmHook,
    ScriptExecutionLog,
    MLModel,
    FaceModelBundle,
    FaceModelArtifact,
    FaceGallery,
    FacePerson,
    FaceGalleryMembership,
    FaceTemplate,
    FaceImportJob,
    Workflow,
    WorkflowNode,
    WorkflowConnection,
    WorkflowTestResult,
    User,
    ApiKey,
    SourceHealthLog,
    SystemSetting,
    FaceEvent,
)


def _schema_lock_timeout_seconds() -> float:
    raw_value = os.getenv('DB_SCHEMA_LOCK_TIMEOUT_SECONDS', '120')
    try:
        return max(1.0, float(raw_value))
    except ValueError as exc:
        raise ValueError(
            'DB_SCHEMA_LOCK_TIMEOUT_SECONDS must be a number greater than zero'
        ) from exc


def _execute_boolean_query(sql: str, params: tuple) -> bool:
    cursor = db.execute_sql(sql, params)
    try:
        row = cursor.fetchone()
        return bool(row and row[0])
    finally:
        cursor.close()


@contextmanager
def _database_schema_lock():
    """Serialize PostgreSQL schema initialization across all processes."""
    if not isinstance(db, pw.PostgresqlDatabase):
        yield
        return

    timeout_seconds = _schema_lock_timeout_seconds()
    deadline = time.monotonic() + timeout_seconds
    acquired = False

    while not acquired:
        acquired = _execute_boolean_query(
            'SELECT pg_try_advisory_lock(%s)',
            (_SCHEMA_LOCK_ID,),
        )
        if acquired:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(
                'Timed out waiting for the PostgreSQL database schema lock '
                f'after {timeout_seconds:g} seconds'
            )
        time.sleep(_SCHEMA_LOCK_POLL_SECONDS)

    try:
        yield
    finally:
        try:
            unlocked = _execute_boolean_query(
                'SELECT pg_advisory_unlock(%s)',
                (_SCHEMA_LOCK_ID,),
            )
            if not unlocked:
                logger.error('PostgreSQL database schema lock was not held during release')
        except Exception:
            # Closing the connection below also releases a session advisory lock.
            logger.exception('Failed to explicitly release the database schema lock')


def ensure_default_admin_user():
    from datetime import datetime
    import hashlib

    User.get_or_create(
        username='admin',
        defaults={
            'password_hash': hashlib.sha256('admin123'.encode()).hexdigest(),
            'role': 'admin',
            'created_at': datetime.now()
        }
    )


def setup_database():
    """Apply the complete schema bootstrap atomically and idempotently."""
    db.connect(reuse_if_open=True)
    try:
        with _database_schema_lock():
            # PostgreSQL supports transactional DDL. SQLite also keeps these
            # schema changes transactional, so a failure cannot leave half of
            # the expected tables behind.
            with db.atomic():
                _apply_schema_changes()
    finally:
        if not db.is_closed():
            db.close()

    logger.info('Database schema initialization completed successfully')
    print('数据库已使用 Peewee 模型初始化。')


def verify_database_schema():
    """Fail fast when a runtime process sees an incomplete database schema."""
    db.connect(reuse_if_open=True)
    try:
        missing_tables = [
            model._meta.table_name
            for model in _DATABASE_MODELS
            if not db.table_exists(model._meta.table_name)
        ]
        if missing_tables:
            raise RuntimeError(
                'Database schema is incomplete; missing tables: '
                f'{", ".join(sorted(missing_tables))}. '
                'Run `python3 -m app.setup_database` before starting services.'
            )

        missing_columns = []
        for model in _DATABASE_MODELS:
            table_name = model._meta.table_name
            actual_columns = {column.name for column in db.get_columns(table_name)}
            expected_columns = {
                field.column_name for field in model._meta.sorted_fields
            }
            for column_name in sorted(expected_columns - actual_columns):
                missing_columns.append(f'{table_name}.{column_name}')

        if missing_columns:
            raise RuntimeError(
                'Database schema is incomplete; missing columns: '
                f'{", ".join(missing_columns)}. '
                'Run `python3 -m app.setup_database` before starting services.'
            )
    finally:
        if not db.is_closed():
            db.close()


def _apply_schema_changes():
    # 旧库必须先补所有新增的索引列，再让 Peewee 创建模型索引。
    # PostgreSQL 会直接拒绝引用不存在列的 CREATE INDEX；SQLite 还可能把
    # 不存在的双引号列当成表达式，留下不可用的索引。
    if db.table_exists(Workflow._meta.table_name):
        _ensure_workflow_columns()
    _ensure_portability_columns(create_indexes=False)
    # 创建所有数据库表，按依赖顺序
    db.create_tables(_DATABASE_MODELS, safe=True)
    _ensure_ownership_columns()
    _ensure_video_source_columns()
    _ensure_model_columns()
    _ensure_workflow_columns()
    _ensure_portability_columns()
    _normalize_existing_records()
    _ensure_workflow_indexes()

    ensure_default_admin_user()


    # 使用 get_or_create 来安全地插入数据，如果已存在则不会重复创建
    # 这样脚本就可以重复运行而不会出错
    # phone_detection_2stage, _ = Algorithm.get_or_create(
    #     name="phone_detection_2stage",
    #     defaults={
    #         'plugin_module': 'target_detection',
    #         'model_json': json.dumps({"models": [
    #             {
    #                 "name": "yolov8-head",
    #                 "path": "/Users/yujian/Downloads/head.pt",
    #                 "class": 0,
    #                 "confidence": 0.6,
    #                 "label_name": "Head",
    #                 "label_color": "#FF0000",
    #                 "expand_width": 0.1,  # 扩展宽度比例
    #                 "expand_height": 0.1  # 扩展高度比例
    #             }, {
    #                 "name": "yolov8-phone",
    #                 "path": "/Users/yujian/Downloads/phone.pt",
    #                 "class": 0,
    #                 "confidence": 0.5,
    #                 "label_name": "Phone",
    #                 "label_color": "#0000FF",
    #                 "expand_width": 0.1,  # 扩展宽度比例
    #                 "expand_height": 0.1  # 扩展高度比例
    #             }
    #         ]}),
    #         'label_name': 'Phone',
    #         'label_color': '#FFFF00',
    #         'interval_seconds': 5
    #     }
    # )
    #
    # person_detection, _ = Algorithm.get_or_create(
    #     name="person_detection",
    #     defaults={
    #         'plugin_module': 'target_detection',
    #         'model_json': json.dumps({"models": [
    #             {
    #                 "name": "yolov8n",
    #                 "path": "/Users/yujian/Downloads/yolov8n.pt",
    #                 "class": 0,
    #                 "confidence": 0.5,
    #                 "label_name": "Person",
    #                 "label_color": "#00FF00",
    #                 "expand_width": 0.1,  # 扩展宽度比例
    #                 "expand_height": 0.1  # 扩展高度比例
    #             }
    #         ]}),
    #         'label_name': 'Person',
    #         'label_color': '#FFFF00',
    #         'interval_seconds': 5
    #     }
    # )
    # face_recognition, _ = Algorithm.get_or_create(
    #     name="face_recognition",
    #     defaults={
    #         'model_json': json.dumps({"models": [
    #             {
    #                 "name": "face_recognition_model_1",
    #                 "path": "/Users/yujian/Downloads/face_recognition_model_1.pt",
    #                 "threshold": 0.6
    #             }
    #         ]}),
    #         'interval_seconds': 5
    #     }
    # )

    # task, _ = Task.get_or_create(
    #     source_code="1201",
    #     defaults={
    #         'name': "大厅人流检测",
    #         'enabled': True,
    #         'buffer_name': "buffer_lobby_1201",
    #         "source_name": "电梯口",
    #         'source_url': "rtsp://admin:codvision120@192.168.201.120:554/Streaming/Channels/1",
    #     }
    # )
    #
    # TaskAlgorithm.get_or_create(
    #     task=task,
    #     algorithm=person_detection,
    #     defaults={
    #         "priority" :1
    #     }
    # )
    #
    # TaskAlgorithm.get_or_create(
    #     task=task,
    #     algorithm=phone_detection_2stage,
    #     defaults={
    #         "priority": 2
    #     }
    # )
    #
    # task, _ = Task.get_or_create(
    #     source_code="1211",
    #     defaults={
    #         'name': "大厅人流检测2",
    #         'buffer_name': "buffer_lobby_1211",
    #         "source_name": "研发门口",
    #         'enabled': True,
    #         'source_url': "rtsp://admin:codvision121@192.168.201.121:554/Streaming/Channels/1",
    #     }
    # )
    #
    # TaskAlgorithm.get_or_create(
    #     task=task,
    #     algorithm=person_detection,
    #     defaults={
    #         "priority" :1
    #     }
    # )
    #
    # TaskAlgorithm.get_or_create(
    #     task=task,
    #     algorithm=phone_detection_2stage,
    #     defaults={
    #         "priority": 1
    #     }
    # )
    #
    # task, _ = Task.get_or_create(
    #     source_code="1231",
    #     defaults={
    #         'name': "展厅门口人员检测",
    #         'buffer_name': "buffer_lobby_1231",
    #         "source_name": "展厅门口",
    #         'enabled': True,
    #         'source_url': "rtsp://admin:codvision123@192.168.201.123:554/Streaming/Channels/1",
    #     }
    # )
    #
    # TaskAlgorithm.get_or_create(
    #     task=task,
    #     algorithm=person_detection,
    #     defaults={
    #         "priority" :1
    #     }
    # )
    # TaskAlgorithm.get_or_create(
    #     task=task,
    #     algorithm=phone_detection_2stage,
    #     defaults={
    #         "priority": 1
    #     }
    # )
    #
    # task, _ = Task.get_or_create(
    #     source_code="1251",
    #     defaults={
    #         'name': "大厅人流检测5",
    #         'source_code': "1251",
    #         'buffer_name': "buffer_lobby_1251",
    #         "source_name": "大厅摄像头5",
    #         'enabled': True,
    #         'source_url': "rtsp://admin:codvision125@192.168.201.125:554/Streaming/Channels/1",
    #         'algorithm': person_detection
    #     }
    # )
    #
    #
    # TaskAlgorithm.get_or_create(
    #     task=task,
    #     algorithm=person_detection,
    #     defaults={
    #         "priority" :1
    #     }
    # )

def _normalize_existing_records():
    Algorithm.update(created_by='admin').where(
        (Algorithm.created_by.is_null(True)) | (Algorithm.created_by == '')
    ).execute()
    VideoSource.update(created_by='admin').where(
        (VideoSource.created_by.is_null(True)) | (VideoSource.created_by == '')
    ).execute()
    Workflow.update(created_by='admin').where(
        (Workflow.created_by.is_null(True)) | (Workflow.created_by == '')
    ).execute()
    # video_source_id 是 workflow_data 的规范化索引字段，运行配置仍以 JSON 图为准。
    from app.core.workflow_runtime import extract_source_id_from_workflow_data
    existing_source_ids = {
        source.id for source in VideoSource.select(VideoSource.id)
    }
    for workflow in Workflow.select():
        source_id = None if workflow.is_template else extract_source_id_from_workflow_data(workflow.data_dict)
        # 视频源删除后，JSON 中可能仍保留旧 ID；不可把悬空引用重新写入外键列。
        if source_id not in existing_source_ids:
            source_id = None
        updates = {}
        if workflow.video_source_id != source_id:
            updates['video_source'] = source_id
        if workflow.is_template and workflow.is_active:
            updates['is_active'] = False
        if workflow.is_template and workflow.source_template_id is not None:
            updates['source_template'] = None
        if updates:
            Workflow.update(**updates).where(Workflow.id == workflow.id).execute()
    ExternalApi.update(created_by='admin').where(
        (ExternalApi.created_by.is_null(True)) | (ExternalApi.created_by == '')
    ).execute()
    MLModel.update(uploaded_by='admin').where(
        (MLModel.uploaded_by.is_null(True)) | (MLModel.uploaded_by == '')
    ).execute()
    WorkflowTestResult.update(created_by='admin').where(
        (WorkflowTestResult.created_by.is_null(True)) | (WorkflowTestResult.created_by == '')
    ).execute()

    alert_table = Alert._meta.table_name
    source_table = VideoSource._meta.table_name
    db.execute_sql(
        f"""
        UPDATE {alert_table}
        SET created_by = COALESCE(
            (
                SELECT {source_table}.created_by
                FROM {source_table}
                WHERE {source_table}.id = {alert_table}.video_source_id
            ),
            'admin'
        )
        WHERE created_by IS NULL OR created_by = '';
        """
    )

    workflow_test_table = WorkflowTestResult._meta.table_name
    workflow_table = Workflow._meta.table_name
    db.execute_sql(
        f"""
        UPDATE {workflow_test_table}
        SET created_by = COALESCE(
            (
                SELECT {workflow_table}.created_by
                FROM {workflow_table}
                WHERE {workflow_table}.id = {workflow_test_table}.workflow_id
            ),
            (
                SELECT {source_table}.created_by
                FROM {source_table}
                WHERE {source_table}.id = {workflow_test_table}.video_source_id
            ),
            'admin'
        )
        WHERE created_by IS NULL OR created_by = '';
        """
    )

def _column_exists(table_name: str, column_name: str) -> bool:
    return any(column.name == column_name for column in db.get_columns(table_name))


def _ensure_text_column(table_name: str, column_name: str, default_value: str):
    if _column_exists(table_name, column_name):
        return

    escaped_default = default_value.replace("'", "''")
    db.execute_sql(
        f"ALTER TABLE {table_name} "
        f"ADD COLUMN {column_name} VARCHAR(255) DEFAULT '{escaped_default}'"
    )


def _ensure_workflow_columns():
    table_name = Workflow._meta.table_name
    if not _column_exists(table_name, 'is_template'):
        db.execute_sql(
            f"ALTER TABLE {table_name} "
            "ADD COLUMN is_template BOOLEAN NOT NULL DEFAULT FALSE"
        )
    for column_name in ('source_template_id', 'video_source_id'):
        if not _column_exists(table_name, column_name):
            db.execute_sql(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} INTEGER NULL"
            )


def _ensure_workflow_indexes():
    table_name = Workflow._meta.table_name
    indexes = db.get_indexes(table_name)
    if not any(tuple(index.columns) == ('is_template',) for index in indexes):
        db.execute_sql(
            f"CREATE INDEX IF NOT EXISTS {table_name}_is_template "
            f"ON {table_name} (is_template)"
        )
    if not any(
        index.unique
        and tuple(index.columns) == ('source_template_id', 'video_source_id')
        for index in indexes
    ):
        db.execute_sql(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {table_name}_template_source_unique "
            f"ON {table_name} (source_template_id, video_source_id)"
        )


def _ensure_ownership_columns():
    # 历史库缺少这些列时，先补齐再做归一化，避免 worker 在 setup_database() 直接退出。
    for table_name, column_name, default_value in (
        (Algorithm._meta.table_name, 'created_by', 'admin'),
        (VideoSource._meta.table_name, 'created_by', 'admin'),
        (ExternalApi._meta.table_name, 'created_by', 'admin'),
        (Workflow._meta.table_name, 'created_by', 'admin'),
        (Alert._meta.table_name, 'created_by', 'admin'),
        (WorkflowTestResult._meta.table_name, 'created_by', 'admin'),
        (ScriptVersion._meta.table_name, 'created_by', 'system'),
        (MLModel._meta.table_name, 'uploaded_by', 'admin'),
    ):
        _ensure_text_column(table_name, column_name, default_value)


def _ensure_video_source_columns():
    _ensure_text_column(
        VideoSource._meta.table_name,
        'source_codec',
        'unknown',
    )
    if not _column_exists(VideoSource._meta.table_name, 'decode_keyframes_only'):
        db.execute_sql(
            f"ALTER TABLE {VideoSource._meta.table_name} "
            "ADD COLUMN decode_keyframes_only BOOLEAN NULL"
        )


def _ensure_model_columns():
    _ensure_text_column(
        MLModel._meta.table_name,
        'model_role',
        '',
    )


def _ensure_nullable_text_column(table_name: str, column_name: str, max_length: int):
    if _column_exists(table_name, column_name):
        return
    db.execute_sql(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} VARCHAR({max_length}) NULL"
    )


def _has_columns_index(
    table_name: str,
    columns: tuple[str, ...],
    *,
    unique: bool | None = None,
) -> bool:
    for index in db.get_indexes(table_name):
        if tuple(index.columns) != columns:
            continue
        if unique is None or bool(index.unique) == unique:
            return True
    return False


def _ensure_portability_columns(*, create_indexes: bool = True):
    resources = (Algorithm, ExternalApi, Workflow, Hook, MLModel)
    for model in resources:
        table_name = model._meta.table_name
        if not db.table_exists(table_name):
            continue
        _ensure_nullable_text_column(table_name, 'portable_id', 36)
        if (
            create_indexes
            and not _has_columns_index(table_name, ('portable_id',), unique=True)
        ):
            db.execute_sql(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {table_name}_portable_id_unique "
                f"ON {table_name} (portable_id)"
            )

    model_table = MLModel._meta.table_name
    if not db.table_exists(model_table):
        return
    _ensure_nullable_text_column(model_table, 'artifact_sha256', 64)
    if (
        create_indexes
        and not _has_columns_index(model_table, ('artifact_sha256',))
    ):
        db.execute_sql(
            f"CREATE INDEX IF NOT EXISTS {model_table}_artifact_sha256 "
            f"ON {model_table} (artifact_sha256)"
        )


if __name__ == "__main__":
    setup_database()
