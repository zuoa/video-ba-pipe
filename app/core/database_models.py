import peewee as pw
import json

# 1. 创建一个数据库实例
# 'check_same_thread=False' 在多线程应用中与SQLite一起使用时是推荐的
db = pw.SqliteDatabase('config.db', pragmas={'check_same_thread': False})

# 2. 创建一个所有模型都会继承的基类
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
    id=pw.AutoField()
    name = pw.CharField()
    enabled = pw.BooleanField(default=True)
    source_url = pw.TextField()
    buffer_name = pw.CharField(unique=True)
    # 使用外键关联到 Algorithm 模型，Peewee 会自动处理 JOIN
    algorithm = pw.ForeignKeyField(Algorithm, backref='tasks')
    status = pw.CharField(default='STOPPED')
    decoder_pid = pw.IntegerField(null=True)
    ai_pid = pw.IntegerField(null=True)