"""
YOLO目标检测脚本模板

参考 TargetDetector 插件实现的脚本版本，支持：
- 单模型/多模型 YOLO 检测
- ROI 热区配置（pre_mask/post_filter 模式）
- 多模型 IoU 分组
- 自定义类别过滤和置信度阈值

使用方法：
1. 复制此文件到 app/user_scripts/detectors/ 目录
2. 修改 SCRIPT_METADATA 中的配置
3. 在 Web UI 中创建算法，配置模型路径和参数
"""
import cv2
import numpy as np


# 脚本元数据（必需）
SCRIPT_METADATA = {
    'name': 'yolo_detector',           # 脚本名称（唯一标识）
    'version': '1.0.0',                # 版本号
    'author': 'Your Name',             # 作者
    'description': 'YOLO目标检测脚本，支持单/多模型、ROI配置',  # 描述
    'dependencies': [                  # 依赖列表
        'opencv-python',
        'numpy',
        'ultralytics'  # YOLO 模型库
    ],
    'timeout': 30,                     # 超时时间（秒）
    'memory_limit': 1024               # 内存限制（MB，YOLO模型较大）
}


def create_roi_mask(frame_shape: tuple, roi_regions: list) -> np.ndarray:
    """
    根据 ROI 热区配置创建掩码

    Args:
        frame_shape: 图像形状 (height, width, channels)
        roi_regions: ROI 热区配置列表

    Returns:
        mask: 二值掩码，热区内为 255，热区外为 0
    """
    if not roi_regions:
        # 如果没有 ROI 配置，返回全白掩码（全画面检测）
        return np.ones((frame_shape[0], frame_shape[1]), dtype=np.uint8) * 255

    # 创建黑色掩码
    mask = np.zeros((frame_shape[0], frame_shape[1]), dtype=np.uint8)

    # 在每个 ROI 区域绘制白色多边形
    for region in roi_regions:
        points = region.get('points', [])
        if len(points) >= 3:
            # 转换为 numpy 数组
            pts = np.array(points, dtype=np.int32)
            # 填充多边形
            cv2.fillPoly(mask, [pts], 255)

    return mask


def apply_roi_mask(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """
    将 ROI 掩码应用到图像上

    Args:
        frame: 原始图像
        mask: ROI 掩码

    Returns:
        masked_frame: 应用掩码后的图像
    """
    # 将掩码应用到每个通道
    masked_frame = cv2.bitwise_and(frame, frame, mask=mask)
    return masked_frame


def filter_detections_by_roi(detections: list, mask: np.ndarray) -> list:
    """
    根据 ROI 掩码过滤检测结果，只保留中心点在 ROI 内的检测

    Args:
        detections: 检测结果列表
        mask: ROI 掩码

    Returns:
        filtered_detections: 过滤后的检测结果
    """
    if mask is None:
        return detections

    filtered = []
    for det in detections:
        box = det.get('box', [])
        if len(box) >= 4:
            # 计算边界框中心点
            center_x = int((box[0] + box[2]) / 2)
            center_y = int((box[1] + box[3]) / 2)

            # 检查中心点是否在 ROI 内
            if (0 <= center_y < mask.shape[0] and
                0 <= center_x < mask.shape[1] and
                mask[center_y, center_x] > 0):
                filtered.append(det)

    return filtered


def calculate_iou(box1: list, box2: list) -> float:
    """
    计算两个边界框的 IoU (Intersection over Union)

    Args:
        box1: [x1, y1, x2, y2]
        box2: [x1, y1, x2, y2]

    Returns:
        IoU 值 (0-1)
    """
    # 计算交集区域
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])

    # 如果没有交集
    if x2 < x1 or y2 < y1:
        return 0.0

    # 计算交集面积
    intersection = (x2 - x1) * (y2 - y1)

    # 计算并集面积
    box1_area = (box1[2] - box1[0]) * (box1[3] - box1[1])
    box2_area = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union = box1_area + box2_area - intersection

    # 计算 IoU
    if union == 0:
        return 0.0

    return intersection / union


def find_multimodel_groups(stages_results: dict, iou_threshold: float = 0.5) -> list:
    """
    使用 IoU 分组多模型检测结果

    Args:
        stages_results: {
            'model_name': {
                'result': YOLO result 对象,
                'model_config': 模型配置字典
            }
        }
        iou_threshold: IoU 阈值，大于此值认为是同一目标

    Returns:
        分组后的检测结果列表
    """
    model_names = list(stages_results.keys())
    if len(model_names) < 2:
        return []

    # 收集所有检测框
    all_detections = []
    for model_name in model_names:
        result = stages_results[model_name]['result']
        model_config = stages_results[model_name]['model_config']

        for det in result.boxes.data.tolist():
            x1, y1, x2, y2, conf, cls = det
            all_detections.append({
                'model_name': model_name,
                'bbox': [x1, y1, x2, y2],
                'confidence': float(conf),
                'class': int(cls),
                'class_name': model_config.get('label_name', 'Object')
            })

    # 使用贪心算法进行分组
    groups = []
    used = set()

    for i, det1 in enumerate(all_detections):
        if i in used:
            continue

        # 创建新组
        group = [det1]
        used.add(i)

        # 查找与当前检测框 IoU 高的其他检测框
        for j, det2 in enumerate(all_detections):
            if j in used or det1['model_name'] == det2['model_name']:
                continue

            iou = calculate_iou(det1['bbox'], det2['bbox'])
            if iou >= iou_threshold:
                group.append(det2)
                used.add(j)

        # 只保留被至少两个模型检测到的目标
        if len(group) >= 2:
            groups.append(group)

    return groups


def init(config):
    """
    初始化函数（可选）

    用于加载 YOLO 模型

    Args:
        config: dict, 算法配置，包含：
            - model_path: str, 模型路径（单模型）
            - models_config: dict, 多模型配置
                {
                    'models': [
                        {
                            'path': 'yolov8n.pt',
                            'name': 'person_model',
                            'class': 0,
                            'confidence': 0.7,
                            'label_name': 'Person',
                            'label_color': '#FF0000'
                        }
                    ]
                }
            - roi_mode: str, ROI 模式 ('post_filter' 或 'pre_mask')
            - iou_threshold: float, IoU 分组阈值 (默认 0.5)
            - 其他数据库字段

    Returns:
        状态字典，包含模型对象
    """
    from ultralytics import YOLO

    print(f"[{SCRIPT_METADATA['name']}] 初始化开始...")

    models = []

    # 支持单模型和多模型配置
    models_config = config.get('models_config', {})

    if 'models' in models_config and len(models_config['models']) > 0:
        # 多模型配置
        for model_cfg in models_config['models']:
            model_path = model_cfg.get('path')
            if model_path:
                print(f"[{SCRIPT_METADATA['name']}] 加载模型: {model_path}")
                model = YOLO(model_path)
                models.append({
                    'model': model,
                    'config': model_cfg
                })
    else:
        # 单模型配置（向后兼容）
        model_path = config.get('model_path', 'yolov8n.pt')
        print(f"[{SCRIPT_METADATA['name']}] 加载模型: {model_path}")
        model = YOLO(model_path)
        models.append({
            'model': model,
            'config': {
                'path': model_path,
                'name': 'default',
                'class': 0,
                'confidence': config.get('confidence', 0.7),
                'label_name': config.get('label_name', 'Object'),
                'label_color': config.get('label_color', '#00FF00')
            }
        })

    state = {
        'models': models,
        'roi_mode': config.get('roi_mode', 'post_filter'),
        'iou_threshold': config.get('iou_threshold', 0.5)
    }

    print(f"[{SCRIPT_METADATA['name']}] 初始化完成，已加载 {len(models)} 个模型")

    return state


def process(frame, config, state=None, roi_regions=None):
    """
    处理函数（必需）

    Args:
        frame: numpy.ndarray, RGB 格式图像 (height, width, 3)
        config: dict, 算法配置
        state: init 函数的返回值
        roi_regions: list, ROI 热区配置（可选）

    Returns:
        dict: {
            'detections': [
                {
                    'box': [x1, y1, x2, y2],    # 边界框坐标
                    'label': 'person',            # 标签名称
                    'confidence': 0.95,           # 置信度
                    'class': 0,                   # 类别 ID
                    'stages': [...]               # 多模型结果（可选）
                }
            ],
            'metadata': {},
            'roi_mask': np.ndarray  # ROI 掩码（用于可视化）
        }
    """
    # 复制帧并转换为 BGR（YOLO 使用 BGR 格式）
    frame_bgr = cv2.cvtColor(frame.copy(), cv2.COLOR_RGB2BGR)

    # 创建 ROI 掩码
    roi_mask = create_roi_mask(frame_bgr.shape, roi_regions) if roi_regions else None

    # ROI 应用模式
    roi_mode = state.get('roi_mode', 'post_filter')

    if roi_mode == 'pre_mask' and roi_mask is not None:
        # 在检测前应用掩码，ROI 外的区域变黑
        frame_to_detect = apply_roi_mask(frame_bgr, roi_mask)
    else:
        # 使用原始图像进行检测，在检测后过滤结果
        frame_to_detect = frame_bgr

    # 执行检测
    models = state.get('models', [])
    stages_results = {}

    for model_info in models:
        model = model_info['model']
        model_cfg = model_info['config']

        conf_thresh = model_cfg.get('confidence', 0.7)
        class_filter = model_cfg.get('class', None)

        try:
            # YOLO 推理
            results = model.predict(
                frame_to_detect,
                save=False,
                classes=[class_filter] if class_filter is not None else None,
                conf=conf_thresh,
                verbose=False
            )

            if results and len(results) > 0:
                stages_results[model_cfg.get('name')] = {
                    'result': results[0],
                    'model_config': model_cfg
                }

        except Exception as e:
            print(f"[{SCRIPT_METADATA['name']}] 模型 {model_cfg.get('name')} 出错: {e}")

    detections = []

    # 如果只有一个模型，直接返回结果
    if len(stages_results) < 2:
        if stages_results:
            first_model_name = list(stages_results.keys())[0]
            first_result = stages_results[first_model_name]['result']
            first_config = stages_results[first_model_name]['model_config']

            for det in first_result.boxes.data.tolist():
                x1, y1, x2, y2, conf, cls = det
                detections.append({
                    'box': [x1, y1, x2, y2],
                    'label': first_config.get('label_name', 'Object'),
                    'confidence': float(conf),
                    'class': int(cls)
                })
    else:
        # 多模型 IoU 分组
        iou_threshold = state.get('iou_threshold', 0.5)
        detection_groups = find_multimodel_groups(stages_results, iou_threshold)

        for group in detection_groups:
            # 计算能包裹所有框的最小外接矩形
            x_min = min(d['bbox'][0] for d in group)
            y_min = min(d['bbox'][1] for d in group)
            x_max = max(d['bbox'][2] for d in group)
            y_max = max(d['bbox'][3] for d in group)

            # 构建 stages 信息
            stages_info = []
            for d in group:
                stages_info.append({
                    'model_name': d['model_name'],
                    'box': d['bbox'],
                    'label': stages_results[d['model_name']]['model_config'].get('label_name', 'Object'),
                    'class': d['class'],
                    'confidence': d['confidence']
                })

            detections.append({
                'box': [x_min, y_min, x_max, y_max],
                'label': config.get('label_name', 'Object'),
                'confidence': np.mean([d['confidence'] for d in group]),
                'class': 0,
                'stages': stages_info
            })

    # 应用 ROI 过滤（仅在 post_filter 模式下需要）
    if roi_mode == 'post_filter' and roi_mask is not None and len(detections) > 0:
        original_count = len(detections)
        detections = filter_detections_by_roi(detections, roi_mask)
        filtered_count = original_count - len(detections)
        if filtered_count > 0:
            print(f"[{SCRIPT_METADATA['name']}] ROI 后过滤：移除了 {filtered_count} 个 ROI 区域外的检测")

    return {
        'detections': detections,
        'metadata': {
            'frame_shape': frame.shape,
            'detection_count': len(detections),
            'models_count': len(models),
            'roi_mode': roi_mode,
            'roi_regions_count': len(roi_regions) if roi_regions else 0
        },
        'roi_mask': roi_mask  # 传递给可视化函数
    }


def cleanup(state):
    """
    清理函数（可选）

    用于释放模型资源

    Args:
        state: init 函数的返回值
    """
    print(f"[{SCRIPT_METADATA['name']}] 清理资源...")

    # 释放模型
    if state and 'models' in state:
        for model_info in state['models']:
            model = model_info.get('model')
            if model:
                del model

    print(f"[{SCRIPT_METADATA['name']}] 资源清理完成")