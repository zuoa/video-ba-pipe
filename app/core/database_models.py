import json

import peewee as pw

from app.config import DB_PATH


class DatabaseConfig:
    """数据库配置类"""

    def __init__(self, db_path: str = 'app.db'):
        self.db_path = db_path
        self.database = pw.SqliteDatabase(db_path, pragmas={'check_same_thread': False})

    def get_database(self):
        return self.database


db_config = DatabaseConfig(DB_PATH)
db = db_config.get_database()


class BaseModel(pw.Model):
    class Meta:
        database = db


class Algorithm(BaseModel):
    name = pw.CharField(unique=True)
    model_json = pw.TextField(default='{}')
    interval_seconds = pw.DoubleField(default=1)
    ext_config_json = pw.TextField(default='{}')
    plugin_module = pw.CharField(max_length=255, null=True)
    label_name = pw.CharField(default='Object')
    label_color = pw.CharField(default='#FF0000')

    @property
    def models_config(self):
        """提供一个方便的方法来获取解析后的JSON配置"""
        return json.loads(self.model_json)


# 2. 定义 Task 模型 (移除 ForeignKeyField)
class Task(BaseModel):
    id = pw.AutoField()
    name = pw.CharField()
    enabled = pw.BooleanField(default=True)
    source_code = pw.CharField(max_length=255, unique=True)
    source_name = pw.CharField(max_length=255, null=True)
    source_url = pw.TextField()
    source_decode_width = pw.IntegerField(default=960)
    source_decode_height = pw.IntegerField(default=540)
    source_fps = pw.IntegerField(default=10)
    buffer_name = pw.CharField(max_length=255, default='video_buffer')

    status = pw.CharField(default='STOPPED')
    decoder_pid = pw.IntegerField(null=True)
    ai_pid = pw.IntegerField(null=True)
    
    # 时间窗口检测配置
    enable_window_check = pw.BooleanField(default=False)
    window_size = pw.IntegerField(default=30)  # 时间窗口大小（秒）
    window_mode = pw.CharField(default='ratio')  # 预警模式: 'count', 'ratio', 'consecutive'
    window_threshold = pw.FloatField(default=0.3)  # 预警阈值


# 3. 引入中间表 TaskAlgorithm 模型
class TaskAlgorithm(BaseModel):
    """
    关联模型，用于实现 Task 和 Algorithm 之间的多对多关系
    """
    task = pw.ForeignKeyField(Task, backref='task_algorithms', on_delete='CASCADE')
    algorithm = pw.ForeignKeyField(Algorithm, backref='task_algorithms', on_delete='CASCADE')
    # 可以在关联表中存储与这次关联相关的额外信息，例如：
    priority = pw.IntegerField(default=0)  # 例如，这个算法在这个任务中的优先级
    config_override_json = pw.TextField(default='{}') # 对该算法在这个任务中的特定配置

    class Meta:
        # 确保一个任务不会重复关联同一个算法
        indexes = (
            (('task', 'algorithm'), True),
        )


class Alert(BaseModel):
    id = pw.AutoField()
    task = pw.ForeignKeyField(Task, backref='alerts')
    alert_time = pw.DateTimeField()
    alert_type = pw.CharField()
    alert_message = pw.TextField(null=True)
    alert_image = pw.TextField(null=True)
    alert_image_ori = pw.TextField(null=True)
    alert_video = pw.TextField(null=True)
    
    # 检测序列相关字段
    detection_count = pw.IntegerField(default=1)  # 本次序列中的检测次数
    window_stats = pw.TextField(null=True)  # 窗口统计信息（JSON字符串）
    detection_images = pw.TextField(null=True)  # 历史检测图片路径（JSON数组）