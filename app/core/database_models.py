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


# 3. 定义 Algorithm 模型 (对应 algorithms 表)
class Algorithm(BaseModel):
    name = pw.CharField(unique=True)
    model_path = pw.TextField()
    # 使用 TextField 存储 JSON 字符串，灵活且强大
    config_json = pw.TextField(default='{}')

    @property
    def config(self):
        """提供一个方便的方法来获取解析后的JSON配置"""
        return json.loads(self.config_json)


# 4. 定义 Task 模型 (对应 tasks 表)
class Task(BaseModel):
    id = pw.AutoField()
    name = pw.CharField()
    enabled = pw.BooleanField(default=True)
    source_code = pw.CharField(max_length=255, unique=True)
    source_name = pw.CharField(max_length=255, null=True)
    source_url = pw.TextField()
    buffer_name = pw.CharField(max_length=255, default='video_buffer')
    # 使用外键关联到 Algorithm 模型，Peewee 会自动处理 JOIN
    algorithm = pw.ForeignKeyField(Algorithm, backref='tasks')
    status = pw.CharField(default='STOPPED')
    decoder_pid = pw.IntegerField(null=True)
    ai_pid = pw.IntegerField(null=True)
