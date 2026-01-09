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
    description = pw.TextField(null=True)

    # === 脚本配置 ===
    script_path = pw.TextField()
    script_config = pw.TextField(default='{}')

    # === 扩展配置（执行配置等） ===
    ext_config_json = pw.TextField(default='{}')

    # === Hook配置 ===
    enabled_hooks = pw.TextField(null=True)

    created_at = pw.DateTimeField(null=True)
    updated_at = pw.DateTimeField(null=True)

    @property
    def config_dict(self):
        """获取解析后的脚本配置"""
        try:
            return json.loads(self.script_config) if self.script_config else {}
        except:
            return {}

    @property
    def ext_config(self):
        """获取解析后的扩展配置"""
        try:
            return json.loads(self.ext_config_json) if self.ext_config_json else {}
        except:
            return {}


class VideoSource(BaseModel):
    """视频源管理"""
    id = pw.AutoField()
    name = pw.CharField()
    enabled = pw.BooleanField(default=True)
    source_code = pw.CharField(max_length=255, unique=True)
    source_url = pw.TextField()
    source_decode_width = pw.IntegerField(default=960)
    source_decode_height = pw.IntegerField(default=540)
    source_fps = pw.IntegerField(default=10)
    status = pw.CharField(default='STOPPED')
    decoder_pid = pw.IntegerField(null=True)

    @property
    def buffer_name(self):
        return f'video_buffer.{self.source_code}'


# ==================== 工作流配置表 ====================

class Workflow(BaseModel):
    """工作流配置表"""
    id = pw.AutoField()
    name = pw.CharField()                    # 工作流名称
    description = pw.TextField(null=True)    # 描述
    workflow_data = pw.TextField(default='{}')  # 工作流数据（JSON）：包含节点和连线
    is_active = pw.BooleanField(default=False)  # 是否激活
    created_at = pw.DateTimeField()
    updated_at = pw.DateTimeField()
    created_by = pw.CharField(default='admin')

    class Meta:
        table_name = 'workflows'

    @property
    def data_dict(self):
        """获取解析后的工作流数据"""
        try:
            if not self.workflow_data:
                return {}
            return json.loads(self.workflow_data)
        except Exception as e:
            import logging
            logging.error(f"解析工作流数据失败 (ID={self.id}): {e}, 原始数据: {self.workflow_data[:200]}")
            return {}


class WorkflowNode(BaseModel):
    """工作流节点配置"""
    id = pw.AutoField()
    workflow = pw.ForeignKeyField(Workflow, backref='nodes', on_delete='CASCADE')
    node_id = pw.CharField()                 # 节点ID（前端生成的UUID）
    node_type = pw.CharField()               # 节点类型：source, algorithm, output, condition
    node_data = pw.TextField(default='{}')  # 节点配置数据（JSON）
    position_x = pw.FloatField(default=0)    # X坐标
    position_y = pw.FloatField(default=0)    # Y坐标

    class Meta:
        table_name = 'workflow_nodes'
        indexes = (
            (('workflow', 'node_id'), True),
        )

    @property
    def data_dict(self):
        """获取解析后的节点数据"""
        try:
            return json.loads(self.node_data) if self.node_data else {}
        except:
            return {}


class WorkflowConnection(BaseModel):
    """工作流连线配置"""
    id = pw.AutoField()
    workflow = pw.ForeignKeyField(Workflow, backref='connections', on_delete='CASCADE')
    from_node_id = pw.CharField()            # 源节点ID
    to_node_id = pw.CharField()              # 目标节点ID
    from_port = pw.CharField(default='output')  # 输出端口：output, true, false
    to_port = pw.CharField(default='input')     # 输入端口：input
    condition = pw.CharField(null=True)      # 条件：detected, not_detected, null
    label = pw.CharField(null=True)          # 连线标签

    class Meta:
        table_name = 'workflow_connections'
        indexes = (
            (('workflow', 'from_node_id', 'to_node_id'), False),
        )


class Alert(BaseModel):
    id = pw.AutoField()
    video_source = pw.ForeignKeyField(VideoSource, backref='alerts', on_delete='CASCADE')
    workflow = pw.ForeignKeyField(Workflow, backref='alerts', on_delete='SET NULL', null=True)
    alert_time = pw.DateTimeField()
    alert_type = pw.CharField()
    alert_level = pw.CharField(default='info')  # 告警级别: info, warning, error, critical
    alert_message = pw.TextField(null=True)
    alert_image = pw.TextField(null=True)
    alert_image_ori = pw.TextField(null=True)
    alert_video = pw.TextField(null=True)

    # 检测序列相关字段
    detection_count = pw.IntegerField(default=1)
    window_stats = pw.TextField(null=True)
    detection_images = pw.TextField(null=True)


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
    video_source = pw.ForeignKeyField(VideoSource, backref='script_execution_logs', on_delete='CASCADE')
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


class User(BaseModel):
    """用户表"""
    id = pw.AutoField()
    username = pw.CharField(unique=True, max_length=50)
    password_hash = pw.CharField(max_length=255)
    role = pw.CharField(max_length=20, default='user')
    created_at = pw.DateTimeField()
    last_login = pw.DateTimeField(null=True)
    enabled = pw.BooleanField(default=True)

    class Meta:
        table_name = 'users'


