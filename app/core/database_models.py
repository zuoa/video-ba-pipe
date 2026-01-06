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
    
    # === 脚本配置 ===
    script_path = pw.TextField()  # 脚本路径（必需）
    script_config = pw.TextField(default='{}')  # 脚本配置JSON（传给init()）
    detector_template_id = pw.IntegerField(null=True)  # 关联的检测器模板ID（可选）
    
    # === 执行配置 ===
    interval_seconds = pw.DoubleField(default=1)
    runtime_timeout = pw.IntegerField(default=30)  # 运行超时（秒）
    memory_limit_mb = pw.IntegerField(default=512)  # 内存限制（MB）
    
    # === 时间窗口检测配置 ===
    enable_window_check = pw.BooleanField(default=False)
    window_size = pw.IntegerField(default=30)  # 时间窗口大小（秒）
    window_mode = pw.CharField(default='ratio')  # 预警模式: 'count', 'ratio', 'consecutive'
    window_threshold = pw.FloatField(default=0.3)  # 预警阈值
    
    # === UI配置 ===
    label_name = pw.CharField(default='Object')
    label_color = pw.CharField(default='#FF0000')
    
    # === Hook配置 ===
    enabled_hooks = pw.TextField(null=True)  # 启用的Hook列表（JSON）

    @property
    def config_dict(self):
        """获取解析后的脚本配置"""
        try:
            return json.loads(self.script_config) if self.script_config else {}
        except:
            return {}


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
    roi_regions = pw.TextField(default='[]')  # 热区配置，JSON数组格式：[{"points": [[x1,y1], [x2,y2], ...], "name": "区域1"}]

    class Meta:
        # 确保一个任务不会重复关联同一个算法
        indexes = (
            (('task', 'algorithm'), True),
        )
    
    @property
    def roi_config(self):
        """提供一个方便的方法来获取解析后的ROI配置"""
        try:
            return json.loads(self.roi_regions)
        except:
            return []


class Alert(BaseModel):
    id = pw.AutoField()
    task = pw.ForeignKeyField(Task, backref='alerts', on_delete='CASCADE')
    alert_time = pw.DateTimeField()
    alert_type = pw.CharField()
    alert_message = pw.TextField(null=True)
    alert_image = pw.TextField(null=True)
    alert_image_ori = pw.TextField(null=True)
    alert_video = pw.TextField(null=True)
    
    # 检测序列相关字段
    detection_count = pw.IntegerField(default=1)  # 本次序列中的检测次数
    window_stats = pw.TextField(null=True)  # 窗口统计信息（JSON字符串）
    detection_images = pw.TextField(null=True)  # 历史检测图片路径（JSON数组)


# ==================== 脚本支持相关表 ====================

class ScriptVersion(BaseModel):
    """脚本版本管理"""
    algorithm = pw.ForeignKeyField(Algorithm, backref='script_versions', on_delete='CASCADE')
    version = pw.CharField()
    script_path = pw.TextField()
    file_hash = pw.CharField()
    content_hash = pw.CharField()
    changelog = pw.TextField(null=True)
    is_active = pw.BooleanField(default=False)
    created_at = pw.DateTimeField()
    created_by = pw.CharField(default='system')

    class Meta:
        indexes = (
            (('algorithm', 'version'), True),
        )


class Hook(BaseModel):
    """Hook定义"""
    name = pw.CharField(unique=True)
    hook_point = pw.CharField()  # 'pre_detect', 'post_detect', 'pre_alert', 'pre_record', 'post_record'
    script_path = pw.TextField()  # Hook脚本路径
    entry_function = pw.CharField(default='execute')
    priority = pw.IntegerField(default=100)
    condition_json = pw.TextField(null=True)  # Hook触发条件（JSON）
    enabled = pw.BooleanField(default=True)
    created_at = pw.DateTimeField()

    @property
    def condition(self):
        """获取解析后的条件配置"""
        try:
            return json.loads(self.condition_json) if self.condition_json else {}
        except:
            return {}


class AlgorithmHook(BaseModel):
    """算法与Hook的关联表"""
    algorithm = pw.ForeignKeyField(Algorithm, backref='algorithm_hooks', on_delete='CASCADE')
    hook = pw.ForeignKeyField(Hook, backref='algorithm_hooks', on_delete='CASCADE')
    enabled = pw.BooleanField(default=True)
    hook_config = pw.TextField(null=True)  # Hook特定配置（JSON）

    class Meta:
        indexes = (
            (('algorithm', 'hook'), True),
        )

    @property
    def config(self):
        """获取解析后的Hook配置"""
        try:
            return json.loads(self.hook_config) if self.hook_config else {}
        except:
            return {}


class ScriptExecutionLog(BaseModel):
    """脚本执行日志"""
    algorithm = pw.ForeignKeyField(Algorithm, backref='execution_logs', on_delete='CASCADE')
    task = pw.ForeignKeyField(Task, backref='script_execution_logs', on_delete='CASCADE')
    hook = pw.ForeignKeyField(Hook, backref='execution_logs', null=True, on_delete='SET NULL')
    script_path = pw.TextField()
    entry_function = pw.CharField()
    execution_time_ms = pw.IntegerField()
    success = pw.BooleanField()
    error_message = pw.TextField(null=True)
    memory_used_mb = pw.IntegerField(null=True)
    result_count = pw.IntegerField(default=0)
    frame_timestamp = pw.FloatField(null=True)
    executed_at = pw.DateTimeField()


# ==================== 检测器模板表 ====================

class DetectorTemplate(BaseModel):
    """检测器模板 - 预设配置"""
    id = pw.AutoField()
    name = pw.CharField(unique=True)            # "人员检测-高精度"
    description = pw.TextField(null=True)       # 描述
    script_path = pw.TextField()                # 脚本路径（相对于USER_SCRIPTS_ROOT）
    config_preset = pw.TextField(default='{}')  # 预设配置（JSON）
    category = pw.CharField(default='detection') # 类别：detection/tracking/classification/custom
    tags = pw.TextField(default='[]')           # 标签（JSON数组）
    is_system = pw.BooleanField(default=False)  # 是否系统模板
    is_enabled = pw.BooleanField(default=True)  # 是否启用
    icon = pw.CharField(null=True)              # 图标（emoji或class名）
    
    created_at = pw.DateTimeField()
    updated_at = pw.DateTimeField()
    created_by = pw.CharField(default='system')
    
    # 统计信息
    usage_count = pw.IntegerField(default=0)    # 使用次数

    class Meta:
        table_name = 'detector_templates'
    
    @property
    def config_dict(self):
        """获取解析后的配置"""
        try:
            return json.loads(self.config_preset) if self.config_preset else {}
        except:
            return {}
    
    @property
    def tags_list(self):
        """获取解析后的标签列表"""
        try:
            return json.loads(self.tags) if self.tags else []
        except:
            return []
    
    def increment_usage(self):
        """增加使用计数"""
        self.usage_count += 1
        self.save()


# ==================== 模型管理相关表 ====================

class MLModel(BaseModel):
    """AI模型文件管理表"""
    id = pw.AutoField()
    name = pw.CharField()                # 模型显示名称，如 "YOLOv8n Person"
    filename = pw.CharField()            # 实际文件名，如 "yolov8n.pt"
    file_path = pw.CharField()           # 完整路径
    file_size = pw.IntegerField()        # 文件大小（字节）
    model_type = pw.CharField()          # 类型：YOLO, ONNX, TensorRT等
    framework = pw.CharField()           # 框架：ultralytics, pytorch, onnx等
    input_shape = pw.CharField(null=True)# 输入尺寸，如 "640x640"
    classes = pw.TextField(null=True)    # 支持的类别JSON，如 {"0": "person", "1": "car"}
    description = pw.TextField(null=True)# 描述
    version = pw.CharField(default='v1.0')# 版本号
    tags = pw.TextField(null=True)       # 标签JSON数组，如 ["person", "detection"]

    created_at = pw.DateTimeField()
    updated_at = pw.DateTimeField()
    uploaded_by = pw.CharField(default='admin') # 上传者

    # 统计信息
    download_count = pw.IntegerField(default=0)
    usage_count = pw.IntegerField(default=0)    # 被多少个算法使用
    enabled = pw.BooleanField(default=True)

    class Meta:
        table_name = 'ml_models'  # 明确指定表名
        indexes = (
            (('name', 'version'), True),  # 名称+版本唯一
        )

    @property
    def classes_dict(self):
        """获取解析后的类别字典"""
        try:
            return json.loads(self.classes) if self.classes else {}
        except:
            return {}

    @property
    def tags_list(self):
        """获取解析后的标签列表"""
        try:
            return json.loads(self.tags) if self.tags else []
        except:
            return []

    def increment_usage(self):
        """增加使用计数"""
        self.usage_count += 1
        self.save()

    def decrement_usage(self):
        """减少使用计数"""
        if self.usage_count > 0:
            self.usage_count -= 1
            self.save()