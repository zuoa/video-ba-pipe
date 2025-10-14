import json

from app.core.database_models import db, Algorithm, Task, TaskAlgorithm, Alert


def setup_database():
    # 连接数据库并创建表
    db.connect()
    db.create_tables([Algorithm, Task, TaskAlgorithm, Alert], safe=True)

    # 使用 get_or_create 来安全地插入数据，如果已存在则不会重复创建
    # 这样脚本就可以重复运行而不会出错
    phone_detection_2stage, _ = Algorithm.get_or_create(
        name="phone_detection_2stage",
        defaults={
            'plugin_module': 'target_detection',
            'model_json': json.dumps({"models": [
                {
                    "name": "yolov8-head",
                    "path": "/Users/yujian/Downloads/head.pt",
                    "confidence": 0.6,
                    "label_name": "Head",
                    "label_color": "#FF0000",
                    "expand_width": 0.1,  # 扩展宽度比例
                    "expand_height": 0.1  # 扩展高度比例
                }, {
                    "name": "yolov8-phone",
                    "path": "/Users/yujian/Downloads/phone.pt",
                    "confidence": 0.5,
                    "label_name": "Phone",
                    "label_color": "#0000FF",
                    "expand_width": 0.1,  # 扩展宽度比例
                    "expand_height": 0.1  # 扩展高度比例
                }
            ]}),
            'label_name': 'Phone',
            'label_color': '#FFFF00',
            'interval_seconds': 5
        }
    )

    person_detection, _ = Algorithm.get_or_create(
        name="person_detection",
        defaults={
            'plugin_module': 'target_detection',
            'model_json': json.dumps({"models": [
                {
                    "name": "yolov8n",
                    "path": "/Users/yujian/Downloads/yolov8n.pt",
                    "confidence": 0.5,
                    "label_name": "Person",
                    "label_color": "#00FF00",
                    "expand_width": 0.1,  # 扩展宽度比例
                    "expand_height": 0.1  # 扩展高度比例
                }
            ]}),
            'label_name': 'Person',
            'label_color': '#FFFF00',
            'interval_seconds': 5
        }
    )
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

    task, _ = Task.get_or_create(
        source_code="1201",
        defaults={
            'name': "大厅人流检测",
            'enabled': True,
            'buffer_name': "buffer_lobby_1201",
            "source_name": "电梯口",
            'source_url': "rtsp://admin:codvision120@192.168.201.120:554/Streaming/Channels/1",
        }
    )

    TaskAlgorithm.get_or_create(
        task=task,
        algorithm=person_detection,
        defaults={
            "priority" :1
        }
    )

    TaskAlgorithm.get_or_create(
        task=task,
        algorithm=phone_detection_2stage,
        defaults={
            "priority": 2
        }
    )

    task, _ = Task.get_or_create(
        source_code="1211",
        defaults={
            'name': "大厅人流检测2",
            'buffer_name': "buffer_lobby_1211",
            "source_name": "研发门口",
            'enabled': True,
            'source_url': "rtsp://admin:codvision121@192.168.201.121:554/Streaming/Channels/1",
        }
    )

    TaskAlgorithm.get_or_create(
        task=task,
        algorithm=person_detection,
        defaults={
            "priority" :1
        }
    )

    TaskAlgorithm.get_or_create(
        task=task,
        algorithm=phone_detection_2stage,
        defaults={
            "priority": 1
        }
    )

    task, _ = Task.get_or_create(
        source_code="1231",
        defaults={
            'name': "展厅门口人员检测",
            'buffer_name': "buffer_lobby_1231",
            "source_name": "展厅门口",
            'enabled': True,
            'source_url': "rtsp://admin:codvision123@192.168.201.123:554/Streaming/Channels/1",
        }
    )

    TaskAlgorithm.get_or_create(
        task=task,
        algorithm=person_detection,
        defaults={
            "priority" :1
        }
    )
    TaskAlgorithm.get_or_create(
        task=task,
        algorithm=phone_detection_2stage,
        defaults={
            "priority": 1
        }
    )
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

    db.close()
    print(f"数据库已使用 Peewee 模型初始化。")


if __name__ == "__main__":
    setup_database()
