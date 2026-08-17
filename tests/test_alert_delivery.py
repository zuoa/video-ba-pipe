import base64
from datetime import datetime, timedelta

from PIL import Image
from peewee import SqliteDatabase

from app.core import alert_delivery
from app.core.database_models import Alert, AlertDeliveryTask, VideoSource, Workflow
from app.core.object_storage import UploadedObject
from app.core.public_media_config import PublicMediaConfig


def _message(alert):
    return {
        "alert_id": alert.id,
        "external_alert_id": f"box-1-{alert.id}",
        "node_id": "box-1",
        "alert_type": alert.alert_type,
        "alert_image_url": "/local/image",
        "alert_image_ori_url": "/local/original",
        "alert_video_url": None,
    }


def _create_alert(tmp_path):
    image_dir = tmp_path / "frames" / "camera"
    image_dir.mkdir(parents=True)
    Image.new("RGB", (1600, 1000), color=(180, 40, 20)).save(image_dir / "alert.jpg")
    source = VideoSource.create(
        name="Camera",
        source_code="camera-1",
        source_url="rtsp://camera/live",
    )
    return Alert.create(
        video_source=source,
        alert_time=datetime.now(),
        alert_type="person",
        alert_image="camera/alert.jpg",
    )


def test_inline_delivery_embeds_bounded_jpeg(tmp_path, monkeypatch):
    test_db = SqliteDatabase(":memory:")
    models = [VideoSource, Workflow, Alert, AlertDeliveryTask]
    with test_db.bind_ctx(models):
        test_db.create_tables(models)
        alert = _create_alert(tmp_path)
        task = AlertDeliveryTask.create(
            alert=alert,
            event_type="alert.created",
            delivery_mode="inline",
            status="pending",
            attempts=0,
            next_attempt_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        config = PublicMediaConfig(delivery_mode="inline", inline_max_bytes=80_000)
        published = []
        monkeypatch.setattr(alert_delivery, "FRAME_SAVE_PATH", str(tmp_path / "frames"))
        monkeypatch.setattr(alert_delivery, "get_public_media_config", lambda: config)
        monkeypatch.setattr(alert_delivery, "format_alert_message", _message)
        monkeypatch.setattr(alert_delivery, "publish_alert_to_mq", lambda event: published.append(event) or True)

        worker = alert_delivery.AlertDeliveryWorker()
        assert worker.run_once() is True

        task = AlertDeliveryTask.get_by_id(task.id)
        assert task.status == "succeeded"
        image = published[0]["media"]["image"]
        assert image["encoding"] == "base64"
        assert len(image["data"].encode("ascii")) <= 80_000
        assert image["size_bytes"] == len(base64.b64decode(image["data"]))
        assert published[0]["alert_image_url"] is None


def test_object_storage_sends_created_then_media_ready(tmp_path, monkeypatch):
    test_db = SqliteDatabase(":memory:")
    models = [VideoSource, Workflow, Alert, AlertDeliveryTask]
    with test_db.bind_ctx(models):
        test_db.create_tables(models)
        alert = _create_alert(tmp_path)
        AlertDeliveryTask.create(
            alert=alert,
            event_type="alert.created",
            delivery_mode="object_storage",
            status="pending",
            attempts=0,
            next_attempt_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        config = PublicMediaConfig(
            delivery_mode="object_storage",
            object_storage_endpoint_url="https://s3.example.com",
            object_storage_bucket="alerts",
            object_storage_access_key_id="key",
            object_storage_secret_access_key="secret",
        )
        published = []
        monkeypatch.setattr(alert_delivery, "FRAME_SAVE_PATH", str(tmp_path / "frames"))
        monkeypatch.setattr(alert_delivery, "get_public_media_config", lambda: config)
        monkeypatch.setattr(alert_delivery, "format_alert_message", _message)
        monkeypatch.setattr(alert_delivery, "get_node_id", lambda: "box-1")
        monkeypatch.setattr(alert_delivery, "publish_alert_to_mq", lambda event: published.append(event) or True)
        monkeypatch.setattr(
            alert_delivery,
            "upload_alert_image",
            lambda *args, **kwargs: UploadedObject(
                object_key=kwargs["object_key"],
                url="https://signed.example/alert.jpg",
                expires_at=datetime(2030, 1, 1),
            ),
        )

        worker = alert_delivery.AlertDeliveryWorker()
        assert worker.run_once() is True
        assert worker.run_once() is True

        assert [event["event_type"] for event in published] == [
            "alert.created",
            "alert.media.ready",
        ]
        assert published[0]["media"]["status"] == "pending"
        assert published[1]["media"]["image"]["url"] == "https://signed.example/alert.jpg"
        assert AlertDeliveryTask.select().where(AlertDeliveryTask.status == "succeeded").count() == 2


def test_failed_publish_is_scheduled_for_retry(tmp_path, monkeypatch):
    test_db = SqliteDatabase(":memory:")
    models = [VideoSource, Workflow, Alert, AlertDeliveryTask]
    with test_db.bind_ctx(models):
        test_db.create_tables(models)
        alert = _create_alert(tmp_path)
        task = AlertDeliveryTask.create(
            alert=alert,
            event_type="alert.created",
            delivery_mode="url",
            status="pending",
            attempts=0,
            next_attempt_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        config = PublicMediaConfig(delivery_mode="url", async_max_attempts=3)
        monkeypatch.setattr(alert_delivery, "get_public_media_config", lambda: config)
        monkeypatch.setattr(alert_delivery, "format_alert_message", _message)
        monkeypatch.setattr(alert_delivery, "publish_alert_to_mq", lambda event: False)

        assert alert_delivery.AlertDeliveryWorker().run_once() is True
        task = AlertDeliveryTask.get_by_id(task.id)
        assert task.status == "retrying"
        assert task.attempts == 1
        assert task.next_attempt_at > datetime.now()


def test_inline_unreadable_image_degrades_to_text_alert(tmp_path, monkeypatch):
    test_db = SqliteDatabase(":memory:")
    models = [VideoSource, Workflow, Alert, AlertDeliveryTask]
    with test_db.bind_ctx(models):
        test_db.create_tables(models)
        alert = _create_alert(tmp_path)
        (tmp_path / "frames" / "camera" / "alert.jpg").unlink()
        task = AlertDeliveryTask.create(
            alert=alert,
            event_type="alert.created",
            delivery_mode="inline",
            status="pending",
            attempts=0,
            next_attempt_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        config = PublicMediaConfig(delivery_mode="inline")
        published = []
        monkeypatch.setattr(alert_delivery, "FRAME_SAVE_PATH", str(tmp_path / "frames"))
        monkeypatch.setattr(alert_delivery, "get_public_media_config", lambda: config)
        monkeypatch.setattr(alert_delivery, "format_alert_message", _message)
        monkeypatch.setattr(alert_delivery, "publish_alert_to_mq", lambda event: published.append(event) or True)

        assert alert_delivery.AlertDeliveryWorker().run_once() is True

        assert AlertDeliveryTask.get_by_id(task.id).status == "succeeded"
        assert published[0]["media"] == {
            "status": "unavailable",
            "image": None,
            "error_code": "inline_unreadable",
        }


def test_stale_processing_task_is_recovered_during_runtime(tmp_path, monkeypatch):
    test_db = SqliteDatabase(":memory:")
    models = [VideoSource, Workflow, Alert, AlertDeliveryTask]
    with test_db.bind_ctx(models):
        test_db.create_tables(models)
        alert = _create_alert(tmp_path)
        task = AlertDeliveryTask.create(
            alert=alert,
            event_type="alert.created",
            delivery_mode="url",
            status="processing",
            attempts=0,
            next_attempt_at=datetime.now(),
            locked_at=datetime.now() - timedelta(seconds=alert_delivery._LEASE_SECONDS + 1),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        config = PublicMediaConfig(delivery_mode="url")
        monkeypatch.setattr(alert_delivery, "get_public_media_config", lambda: config)
        monkeypatch.setattr(alert_delivery, "format_alert_message", _message)
        monkeypatch.setattr(alert_delivery, "publish_alert_to_mq", lambda event: True)

        assert alert_delivery.AlertDeliveryWorker().run_once() is True
        assert AlertDeliveryTask.get_by_id(task.id).status == "succeeded"
