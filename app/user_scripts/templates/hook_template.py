"""
Hook脚本开发模板

Hook在特定时机执行，可以修改帧、过滤检测结果、触发自定义操作等。
适用于需要在检测流程中插入自定义逻辑的场景。
运行时主链以 NV12 为主格式；Hook 收到的图像帧是系统按需转换后的 RGB 视图。

Hook执行点：
- pre_detect: 检测前，可以修改输入帧
- post_detect: 检测后，可以过滤或增强检测结果
- pre_alert: 告警前，可以决定是否触发告警
- pre_record: 录像前，可以修改录像参数
- post_record: 录像后，可以进行后处理

作者: System
版本: v1.1
更新: 2025-01-08
"""

import cv2
import numpy as np
from typing import Any, Dict, List, Optional

from app.user_scripts.common.result import validate_detections

# ==================== 脚本元数据（必需） ====================

SCRIPT_METADATA = {
    # === 基础信息 ===
    "name": "my_hook",
    "version": "v1.0",
    "description": "自定义Hook描述",
    "author": "Your Name",
    "category": "hook",
    "tags": ["hook", "custom"],
    
    # === Hook配置 ===
    "hook_point": "pre_detect",              # Hook点（必需）
                                              # 可选值：pre_detect, post_detect, pre_alert, pre_record, post_record
    
    "priority": 100,                          # 优先级（可选）
                                              # 数字越小越先执行，默认100
    
    # === 触发条件（可选） ===
    # 定义Hook触发条件，不满足条件则跳过
    "condition": {
        "algorithm_ids": [],                  # 仅对指定算法ID生效，空=所有算法
        "video_source_ids": [],               # 仅对指定视频源ID生效，空=所有视频源
        "time_range": None,                   # 时间范围，如 {"start": "08:00", "end": "18:00"}
        "detection_count_min": None,          # 最小检测数量
        "detection_count_max": None           # 最大检测数量
    },
    
    # === 配置模式（可选） ===
    # 如果Hook需要用户配置参数，可以定义配置模式
    "config_schema": {
        "enable_enhancement": {
            "type": "boolean",
            "label": "启用图像增强",
            "default": True
        },
        "enhancement_factor": {
            "type": "float",
            "label": "增强系数",
            "default": 1.2,
            "min": 0.5,
            "max": 2.0,
            "step": 0.1
        }
    },
    
    # === 依赖列表（可选） ===
    "dependencies": [
        "opencv-python>=4.5.0",
        "numpy>=1.19.0"
    ]
}


# ==================== Hook执行函数 ====================

def execute(context: dict) -> dict:
    """
    Hook执行函数（必需）
    
    根据不同的 hook_point，context 包含不同的字段，返回格式也不同。
    
    Args:
        context: dict, 上下文信息，包含：
        
            【所有Hook点共有】
            - hook_point: str, Hook点名称
            - timestamp: float, 时间戳
            - video_source_id: int, 视频源ID
            - algorithm_id: int, 算法ID (部分Hook点有)
            - config: dict, Hook配置
            
            【pre_detect】
            - frame: np.ndarray, RGB格式输入帧（Hook 当前运行在转换后的可写 RGB 视图上）
            
            【post_detect】
            - frame: np.ndarray, RGB格式原始帧（由 NV12 主帧按需转换）
            - detections: list, 检测结果列表
            - detection_count: int, 检测数量
            
            【pre_alert】
            - frame: np.ndarray, RGB格式原始帧（由 NV12 主帧按需转换）
            - detections: list, 检测结果列表
            - alert_type: str, 告警类型
            - alert_message: str, 告警消息
            
            【pre_record / post_record】
            - video_path: str, 录像文件路径
            - duration: float, 录像时长
            
    Returns:
        dict: 返回结果，格式：
        
            【pre_detect】
            {
                'frame': modified_frame,      # 修改后的帧（可选）
                'skip': False,                 # 是否跳过检测（可选）
                'metadata': {}                 # 额外信息（可选）
            }
            
            【post_detect】
            {
                'detections': modified_detections,  # 修改后的检测结果（可选）
                'skip': False,                       # 是否跳过后续处理（可选）
                'metadata': {}
            }
            
            【pre_alert】
            {
                'skip': False,                 # 是否跳过告警（可选）
                'alert_message': modified_msg, # 修改后的告警消息（可选）
                'metadata': {}
            }
            
    注意事项：
    1. 如果不需要修改，返回空dict {}
    2. skip=True 会跳过后续处理
    3. 处理异常要妥善，不要中断主流程
    """
    from app import logger
    
    hook_point = context.get('hook_point', '')
    
    # 根据不同的Hook点执行不同的逻辑
    if hook_point == 'pre_detect':
        return handle_pre_detect(context)
    elif hook_point == 'post_detect':
        return handle_post_detect(context)
    elif hook_point == 'pre_alert':
        return handle_pre_alert(context)
    elif hook_point == 'pre_record':
        return handle_pre_record(context)
    elif hook_point == 'post_record':
        return handle_post_record(context)
    else:
        logger.warning(f"[Hook] 未知的Hook点: {hook_point}")
        return {}


# ==================== Hook点处理函数 ====================

def handle_pre_detect(context: dict) -> dict:
    """
    Pre-Detect Hook: 检测前处理
    
    使用场景：
    - 图像预处理：去噪、增强、调整尺寸
    - 条件跳过：根据时间、亮度等条件决定是否检测
    - 区域裁剪：只检测特定区域
    - 格式转换：调整图像格式或色彩空间
    
    Args:
        context: 包含 frame, timestamp, video_source_id 等
        
    Returns:
        {'frame': modified_frame, 'skip': False, 'metadata': {}}
    """
    from app import logger
    
    frame = context['frame']
    config = context.get('config', {})
    
    # ===== 在这里实现你的预处理逻辑 =====
    
    # 示例1：图像增强
    # if config.get('enable_enhancement', True):
    #     factor = config.get('enhancement_factor', 1.2)
    #     enhanced = cv2.convertScaleAbs(frame, alpha=factor, beta=0)
    #     return {'frame': enhanced}
    
    # 示例2：条件跳过
    # brightness = frame.mean()
    # if brightness < 10:  # 太暗，跳过检测
    #     logger.debug("[Hook] 图像太暗，跳过检测")
    #     return {'skip': True, 'metadata': {'reason': 'too dark'}}
    
    # 示例3：调整尺寸
    # height, width = frame.shape[:2]
    # if width > 1920:
    #     scale = 1920 / width
    #     resized = cv2.resize(frame, (0, 0), fx=scale, fy=scale)
    #     return {'frame': resized}
    
    # 示例4：裁剪区域
    # cropped = frame[100:900, 200:1600]
    # return {'frame': cropped}
    
    # 示例5：去噪
    # denoised = cv2.fastNlMeansDenoisingColored(frame, None, 10, 10, 7, 21)
    # return {'frame': denoised}
    
    # 不修改，返回空dict
    return {}


def handle_post_detect(context: dict) -> dict:
    """
    Post-Detect Hook: 检测后处理
    
    使用场景：
    - 结果过滤：根据位置、大小、置信度过滤
    - 结果增强：添加额外信息、合并结果
    - 条件告警：只在特定条件下触发告警
    - 统计分析：计数、轨迹分析
    
    Args:
        context: 包含 frame, detections, detection_count 等
        
    Returns:
        {'detections': modified_detections, 'skip': False, 'metadata': {}}
    """
    from app import logger
    
    detections = context.get('detections', [])
    detections = validate_detections(detections)
    frame = context.get('frame')
    
    # ===== 在这里实现你的后处理逻辑 =====
    
    # 示例1：置信度过滤
    # filtered = [d for d in detections if d.get('confidence', 0) > 0.8]
    # return {'detections': filtered}
    
    # 示例2：大小过滤（只保留大目标）
    # filtered = []
    # for det in detections:
    #     box = det.get('box', [])
    #     if len(box) >= 4:
    #         width = box[2] - box[0]
    #         height = box[3] - box[1]
    #         area = width * height
    #         if area > 10000:  # 面积大于10000像素
    #             filtered.append(det)
    # return {'detections': filtered}
    
    # 示例3：位置过滤（只保留画面中心的目标）
    # if frame is not None:
    #     h, w = frame.shape[:2]
    #     center_x, center_y = w // 2, h // 2
    #     margin = 200
    #     
    #     filtered = []
    #     for det in detections:
    #         box = det.get('box', [])
    #         if len(box) >= 4:
    #             det_center_x = (box[0] + box[2]) / 2
    #             det_center_y = (box[1] + box[3]) / 2
    #             
    #             if (abs(det_center_x - center_x) < margin and 
    #                 abs(det_center_y - center_y) < margin):
    #                 filtered.append(det)
    #     
    #     return {'detections': filtered}
    
    # 示例4：条件跳过（检测数量过多，可能是误检）
    # if len(detections) > 20:
    #     logger.warning("[Hook] 检测数量异常，跳过处理")
    #     return {'skip': True, 'metadata': {'reason': 'too many detections'}}
    
    # 示例5：添加额外信息
    # for det in detections:
    #     box = det.get('box', [])
    #     if len(box) >= 4:
    #         width = box[2] - box[0]
    #         height = box[3] - box[1]
    #         det['metadata'] = {
    #             'width': width,
    #             'height': height,
    #             'area': width * height
    #         }
    # return {'detections': detections}
    
    return {}


def handle_pre_alert(context: dict) -> dict:
    """
    Pre-Alert Hook: 告警前处理
    
    使用场景：
    - 告警过滤：根据条件决定是否告警
    - 消息修改：自定义告警消息
    - 外部通知：调用第三方API发送通知
    - 告警合并：避免频繁告警
    
    Args:
        context: 包含 frame, detections, alert_type, alert_message 等
        
    Returns:
        {'skip': False, 'alert_message': modified_msg, 'metadata': {}}
    """
    from app import logger
    
    detections = context.get('detections', [])
    alert_message = context.get('alert_message', '')
    
    # ===== 在这里实现你的告警处理逻辑 =====
    
    # 示例1：条件跳过（只在工作时间告警）
    # import datetime
    # now = datetime.datetime.now()
    # if now.hour < 8 or now.hour > 18:
    #     logger.debug("[Hook] 非工作时间，跳过告警")
    #     return {'skip': True, 'metadata': {'reason': 'outside work hours'}}
    
    # 示例2：修改告警消息
    # count = len(detections)
    # modified_msg = f"检测到 {count} 个目标！{alert_message}"
    # return {'alert_message': modified_msg}
    
    # 示例3：外部通知
    # try:
    #     import requests
    #     requests.post('https://api.example.com/alert', json={
    #         'message': alert_message,
    #         'count': len(detections),
    #         'timestamp': context.get('timestamp')
    #     }, timeout=3)
    # except Exception as e:
    #     logger.error(f"[Hook] 发送外部通知失败: {e}")
    
    # 示例4：告警频率限制
    # last_alert_time = getattr(handle_pre_alert, 'last_time', 0)
    # current_time = context.get('timestamp', 0)
    # if current_time - last_alert_time < 60:  # 60秒内只告警一次
    #     return {'skip': True, 'metadata': {'reason': 'rate limit'}}
    # handle_pre_alert.last_time = current_time
    
    return {}


def handle_pre_record(context: dict) -> dict:
    """
    Pre-Record Hook: 录像前处理
    
    使用场景：
    - 修改录像参数
    - 条件跳过录像
    - 自定义录像路径
    
    Args:
        context: 包含 video_path, duration 等
        
    Returns:
        {'skip': False, 'metadata': {}}
    """
    return {}


def handle_post_record(context: dict) -> dict:
    """
    Post-Record Hook: 录像后处理
    
    使用场景：
    - 视频压缩
    - 上传到云存储
    - 生成缩略图
    - 视频分析
    
    Args:
        context: 包含 video_path, duration 等
        
    Returns:
        {'metadata': {}}
    """
    return {}


# ==================== 开发指南 ====================
"""
📖 开发自定义Hook指南：

1. Hook执行时机：
   检测流程：视频帧 → [pre_detect] → 检测 → [post_detect] → 
            条件判断 → [pre_alert] → 告警 → [pre_record] → 
            录像 → [post_record]

2. 选择合适的Hook点：
   - pre_detect: 需要修改输入帧
   - post_detect: 需要过滤或修改检测结果
   - pre_alert: 需要控制告警行为
   - pre/post_record: 需要处理录像

3. 返回格式规范：
   - 不修改：返回 {}
   - 跳过处理：返回 {'skip': True}
   - 修改数据：返回对应字段，如 {'frame': modified_frame}
   - 添加信息：返回 {'metadata': {'key': 'value'}}

4. 性能考虑：
   - Hook会在每一帧/每次检测执行，必须快速
   - 避免耗时操作（网络请求、文件IO等）
   - 考虑使用异步处理
   - 添加超时控制

5. 错误处理：
   - 使用try-except包裹所有外部调用
   - 不要让Hook异常影响主流程
   - 记录错误日志
   - 提供降级方案

6. 调试技巧：
   - 使用logger记录关键信息
   - 在metadata中返回调试数据
   - 测试各种边界情况
   - 查看日志文件：/data/logs/debug.log

7. 最佳实践：
   - 明确Hook的目的和功能
   - 保持逻辑简单清晰
   - 详细注释代码
   - 提供合理的默认行为
   - 考虑配置化而非硬编码

💡 常见Hook使用案例：
- 夜间模式：pre_detect检测亮度，太暗时增强
- 工作时间：pre_alert只在工作时间告警
- 大小过滤：post_detect过滤太小的目标
- 区域限制：post_detect只保留特定区域的检测
- 外部通知：pre_alert调用第三方API
- 视频上传：post_record上传到云存储
"""
