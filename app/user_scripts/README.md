# 用户脚本开发指南

## 目录结构

```
app/user_scripts/
├── detectors/           # 检测类脚本
├── filters/             # 过滤类脚本
├── postprocessors/      # 后处理脚本
├── hooks/               # Hook脚本
│   ├── pre_detect_hooks/
│   └── post_detect_hooks/
├── utils/               # 工具函数库
├── templates/           # 脚本模板
└── tests/               # 脚本测试
```

## 脚本接口规范

### 检测脚本 (detectors/)

检测脚本必须实现 `process(frame, config, state=None)` 函数：

```python
import cv2
import numpy as np

# 脚本元数据
SCRIPT_METADATA = {
    'name': 'my_detector',
    'version': '1.0.0',
    'author': 'your_name',
    'description': 'My custom detection script',
    'dependencies': ['opencv-python', 'numpy'],
    'timeout': 30,  # 超时时间（秒）
    'memory_limit': 512  # 内存限制（MB）
}

def init(config):
    """
    初始化函数（可选）
    用于加载模型等初始化操作

    Args:
        config: dict, 算法配置

    Returns:
        任意对象，会被传递给process函数
    """
    # 加载模型
    model = load_your_model(config['model_path'])
    return {'model': model}

def process(frame, config, state=None):
    """
    处理函数（必须）

    Args:
        frame: numpy.ndarray, RGB格式图像 (height, width, 3)
        config: dict, 算法配置
        state: init函数的返回值

    Returns:
        dict: {
            'detections': [
                {
                    'box': [x1, y1, x2, y2],  # 边界框坐标
                    'label': 'person',         # 标签名称
                    'confidence': 0.95,        # 置信度
                    'class': 0,                # 类别ID
                    'metadata': {}             # 可选的额外信息
                }
            ],
            'metadata': {},      # 调试信息
            'skip_next': False   # 是否跳过后续处理
        }
    """
    # 获取模型
    model = state['model']

    # 转换为BGR（如果需要）
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
#
    # 执行检测
    results = model.detect(frame_bgr)

    # 格式化结果
    detections = []
    for r in results:
        detections.append({
            'box': [r.x1, r.y1, r.x2, r.y2],
            'label': r.label,
            'confidence': r.confidence,
            'class': r.class_id
        })

    return {'detections': detections}

def cleanup(state):
    """
    清理函数（可选）
    用于释放资源
    """
    if 'model' in state:
        state['model'].release()
```

### 过滤脚本 (filters/)

过滤脚本用于过滤检测结果：

```python
SCRIPT_METADATA = {
    'name': 'confidence_filter',
    'version': '1.0.0',
    'description': 'Filter detections by confidence'
}

def process(detections, frame, context):
    """
    Args:
        detections: list, 检测结果列表
        frame: numpy.ndarray, 原始帧
        context: dict, 上下文信息

    Returns:
        list: 过滤后的detections
    """
    min_conf = context.get('min_confidence', 0.7)

    filtered = [d for d in detections if d['confidence'] >= min_conf]
    return filtered
```

### Hook脚本 (hooks/)

Hook脚本在特定时机执行：

```python
SCRIPT_METADATA = {
    'name': 'frame_enhancer',
    'version': '1.0.0',
    'description': 'Enhance frame before detection',
    'hook_point': 'pre_detect',  # 或 'post_detect'
    'priority': 100
}

def execute(context):
    """
    Hook执行函数

    Args:
        context: dict, 包含 frame, task_id, algorithm_id 等

    Returns:
        dict: {
            'frame': modified_frame,  # 修改后的frame
            'metadata': {},
            'skip': False  # 是否跳过后续处理
        }
    """
    frame = context['frame']

    # 增强图像
    enhanced = enhance_image(frame)

    return {
        'frame': enhanced,
        'metadata': {'brightness': calculate_brightness(enhanced)}
    }
```

## 脚本配置

在数据库中配置脚本算法：

1. 通过Web UI或直接SQL插入：
```sql
INSERT INTO algorithms (
    name, plugin_module, script_type, script_path,
    entry_function, runtime_timeout, memory_limit_mb
) VALUES (
    'custom_person', 'script_algorithm', 'script',
    'detectors/custom_person.py', 'process', 30, 512
);
```

2. 关联到任务：
```sql
INSERT INTO task_algorithm (task_id, algorithm_id)
VALUES (1, 1);
```

## 安全限制

脚本运行在沙箱环境中，有以下限制：
- 禁止使用 `eval()`, `exec()`, `__import__`
- 禁止文件系统访问（除指定目录）
- 禁止网络请求
- 禁止子进程创建
- CPU时间限制
- 内存使用限制

## 调试技巧

1. 查看日志：
```bash
tail -f logs/ai_worker.log | grep ScriptAlgorithm
```

2. 测试脚本：
```python
from app.core.script_loader import get_script_loader

loader = get_script_loader()
module, metadata = loader.load('detectors/my_detector.py')
```

3. 验证语法：
```bash
python -m py_compile app/user_scripts/detectors/my_detector.py
```

## 最佳实践

1. **性能优化**
   - 在 `init()` 中加载重型资源（模型、配置等）
   - 避免在 `process()` 中重复初始化
   - 使用缓存减少重复计算

2. **错误处理**
   - 捕获并记录异常
   - 返回空结果而非抛出异常
   - 使用日志调试

3. **资源管理**
   - 在 `cleanup()` 中释放资源
   - 避免内存泄漏
   - 监控内存使用

4. **测试**
   - 在 `tests/` 目录编写单元测试
   - 测试边界情况
   - 测试性能

## 示例脚本

查看 `templates/` 目录中的模板文件：
- `detector_template.py` - 基础检测脚本模板
- `yolo_detector.py` - YOLO 目标检测脚本（推荐，参考 TargetDetector 实现）
- `simple_detector.py` - 简单颜色检测示例
- `hook_template.py` - Hook 脚本模板

## YOLO 检测器模板使用指南

`yolo_detector.py` 是一个功能完整的目标检测脚本模板，参考了 `TargetDetector` 插件的实现。

### 主要特性

1. **单模型/多模型支持**
   - 支持加载多个 YOLO 模型进行联合检测
   - 使用 IoU (Intersection over Union) 算法进行多模型结果分组
   - 可配置每个模型的类别、置信度阈值、标签名称和颜色

2. **ROI 热区配置**
   - `pre_mask` 模式：检测前应用掩码（性能更好）
   - `post_filter` 模式：检测后过滤结果（精度更高）
   - 支持多边形 ROI 区域

3. **灵活配置**
   - 支持单模型和多模型配置
   - 可调整 IoU 分组阈值
   - 支持类别过滤和自定义标签

### 配置示例

**单模型配置：**
```json
{
    "model_path": "yolov8n.pt",
    "confidence": 0.7,
    "label_name": "Person",
    "label_color": "#FF0000",
    "roi_mode": "post_filter"
}
```

**多模型配置：**
```json
{
    "label_name": "Person",
    "roi_mode": "post_filter",
    "iou_threshold": 0.5,
    "models_config": {
        "models": [
            {
                "path": "yolov8n.pt",
                "name": "person_model_1",
                "class": 0,
                "confidence": 0.7,
                "label_name": "Person Model 1",
                "label_color": "#FF0000"
            },
            {
                "path": "yolov8s.pt",
                "name": "person_model_2",
                "class": 0,
                "confidence": 0.6,
                "label_name": "Person Model 2",
                "label_color": "#00FF00"
            }
        ]
    }
}
```

### 使用步骤

1. **复制模板**
   ```bash
   cp app/user_scripts/templates/yolo_detector.py app/user_scripts/detectors/my_yolo_detector.py
   ```

2. **修改元数据**
   编辑 `SCRIPT_METADATA` 中的 `name`、`version` 等字段

3. **下载 YOLO 模型**
   ```bash
   # 自动下载（首次运行时）
   # 或手动下载到 models/ 目录
   wget https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt
   ```

4. **创建算法记录**
   通过 Web UI 或 SQL 插入：
   ```sql
   INSERT INTO algorithms (
       name, plugin_module, script_type, script_path,
       entry_function, runtime_timeout, memory_limit_mb,
       model_path, models_config
   ) VALUES (
       'my_yolo_person', 'script_algorithm', 'script',
       'detectors/my_yolo_detector.py', 'process', 30, 1024,
       'yolov8n.pt', '{"models": [{"path": "yolov8n.pt", "name": "person", "class": 0, "confidence": 0.7}]}'
   );
   ```

5. **关联任务并配置 ROI**（可选）

### 返回值格式

```python
{
    'detections': [
        {
            'box': [x1, y1, x2, y2],  # 边界框坐标
            'label': 'Person',         # 标签名称
            'confidence': 0.85,        # 置信度
            'class': 0,                # 类别 ID
            'stages': [                # 多模型结果（可选）
                {
                    'model_name': 'person_model_1',
                    'box': [x1, y1, x2, y2],
                    'label': 'Person Model 1',
                    'confidence': 0.87
                }
            ]
        }
    ],
    'metadata': {
        'frame_shape': (1080, 1920, 3),
        'detection_count': 3,
        'models_count': 2,
        'roi_mode': 'post_filter',
        'roi_regions_count': 1
    },
    'roi_mask': numpy.ndarray  # ROI 掩码
}
```
