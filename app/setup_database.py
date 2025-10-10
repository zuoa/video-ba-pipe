import json

from app.core.database_models import db, Algorithm, Task


def setup_database():
    # 连接数据库并创建表
    db.connect()
    db.create_tables([Algorithm, Task])

    # 使用 get_or_create 来安全地插入数据，如果已存在则不会重复创建
    # 这样脚本就可以重复运行而不会出错
    person_detection, _ = Algorithm.get_or_create(
        name="person_detection",
        defaults={
            'model_path': "/path/to/yolov8_person.pt",
            'config_json': json.dumps({"confidence": 0.5})
        }
    )

    face_recognition, _ = Algorithm.get_or_create(
        name="face_recognition",
        defaults={
            'model_path': "/path/to/arcface_model.onnx",
            'config_json': json.dumps({"threshold": 0.7})
        }
    )

    Task.get_or_create(
        buffer_name="buffer_lobby",
        defaults={
            'name': "大厅人流检测",
            'enabled': True,
            'source_url': "rtsp://admin:codvision120@192.168.201.120:554/Streaming/Channels/1",
            'algorithm': person_detection
        }
    )

    db.close()
    print(f"数据库已使用 Peewee 模型初始化。")


if __name__ == "__main__":
    setup_database()
