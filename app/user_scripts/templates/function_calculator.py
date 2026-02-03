import numpy as np
from typing import Any, Dict, List

from app.user_scripts.common.result import build_result

# 单输入函数列表（只需一个输入节点，依赖 frame 尺寸）
SINGLE_INPUT_FUNCTIONS = [
    'height_ratio_frame',
    'width_ratio_frame',
    'area_ratio_frame',
    'size_absolute'
]

SCRIPT_METADATA = {
    "name": "函数计算器",
    "version": "v1.1",
    "description": "多目标关系计算函数和单输入尺寸计算函数，支持面积比、高度比、距离等",
    "author": "system",
    "category": "function",
    "tags": ["function", "calculator", "multi-input", "single-input"],

    "config_schema": {
        "function_name": {
            "type": "select",
            "label": "计算函数",
            "required": True,
            "options": [
                # 双输入函数
                {"value": "area_ratio", "label": "面积比（双输入）"},
                {"value": "height_ratio", "label": "高度比（双输入）"},
                {"value": "width_ratio", "label": "宽度比（双输入）"},
                {"value": "iou_check", "label": "IOU检查（双输入）"},
                {"value": "distance_check", "label": "距离检查（双输入）"},
                # 单输入函数
                {"value": "height_ratio_frame", "label": "高度占图片比例（单输入）"},
                {"value": "width_ratio_frame", "label": "宽度占图片比例（单输入）"},
                {"value": "area_ratio_frame", "label": "面积占图片比例（单输入）"},
                {"value": "size_absolute", "label": "绝对尺寸检测（单输入）"}
            ],
            "default": "area_ratio"
        },
        "input_a": {
            "type": "object",
            "label": "输入A配置",
            "properties": {
                "node_id": {"type": "string", "label": "节点ID"},
                "class_filter": {"type": "int_list", "label": "类别过滤", "default": []}
            }
        },
        "input_b": {
            "type": "object",
            "label": "输入B配置（仅双输入函数需要）",
            "properties": {
                "node_id": {"type": "string", "label": "节点ID"},
                "class_filter": {"type": "int_list", "label": "类别过滤", "default": []}
            }
        },
        "threshold": {
            "type": "float",
            "label": "阈值",
            "default": 0.7,
            "min": 0.0,
            "max": 1000.0,
            "step": 0.01,
            "description": "对于比例函数，值为0-1之间；对于绝对尺寸函数，值为像素值"
        },
        "operator": {
            "type": "select",
            "label": "比较运算符",
            "options": [
                {"value": "less_than", "label": "小于"},
                {"value": "greater_than", "label": "大于"},
                {"value": "equal", "label": "等于"}
            ],
            "default": "less_than"
        },
        "dimension": {
            "type": "select",
            "label": "维度（仅用于size_absolute）",
            "options": [
                {"value": "height", "label": "高度"},
                {"value": "width", "label": "宽度"},
                {"value": "area", "label": "面积"}
            ],
            "default": "height",
            "visible_when": {"function_name": "size_absolute"}
        }
    },

    "performance": {
        "timeout": 5,
        "memory_limit_mb": 128,
        "gpu_required": False,
        "estimated_time_ms": 5
    },

    "dependencies": [
        "numpy>=1.19.0"
    ]
}


def init(config: dict) -> Dict[str, Any]:
    from app.core.builtin_functions import BUILTIN_FUNCTIONS

    function_name = config.get('function_name', 'area_ratio')
    if function_name not in BUILTIN_FUNCTIONS:
        raise ValueError(f"未知的函数: {function_name}")

    return {
        'function_name': function_name,
        'function': BUILTIN_FUNCTIONS[function_name],
        'is_single_input': function_name in SINGLE_INPUT_FUNCTIONS
    }


def process(frame: np.ndarray, config: dict, roi_regions: list = None,
            state: dict = None, upstream_results: dict = None) -> dict:
    from app import logger

    def _empty_result():
        res = build_result([], metadata={})
        res['function_results'] = []
        return res

    if not state or 'function' not in state:
        return _empty_result()

    if not upstream_results:
        logger.debug(f"[函数计算器] 没有上游结果")
        return _empty_result()

    # 获取第一个上游节点的检测结果（自动识别）
    upstream_node_ids = list(upstream_results.keys())
    if not upstream_node_ids:
        logger.warning(f"[函数计算器] upstream_results 为空字典")
        return _empty_result()

    node_a_id = upstream_node_ids[0]
    result_a = upstream_results.get(node_a_id, {})
    detections_a = result_a.get('detections', [])

    logger.debug(f"[函数计算器] 自动使用上游节点 {node_a_id}，检测到 {len(detections_a)} 个目标")

    # 如果配置了 class_filter，应用过滤
    input_a_config = config.get('input_a', {})
    class_filter_a = input_a_config.get('class_filter', [])
    if class_filter_a:
        filtered_count = len(detections_a)
        detections_a = [d for d in detections_a if d.get('class') in class_filter_a]
        logger.debug(f"[函数计算器] 应用类别过滤后：{filtered_count} -> {len(detections_a)}")

    if not detections_a:
        return _empty_result()

    # 判断是单输入还是双输入函数
    is_single_input = state.get('is_single_input', False)

    if is_single_input:
        # 单输入函数：只需要 detections_a，并传递 frame 尺寸
        frame_height, frame_width = frame.shape[:2]

        function_config = {
            'threshold': config.get('threshold', 0.3),
            'operator': config.get('operator', 'greater_than'),
            'frame_height': frame_height,
            'frame_width': frame_width,
            'dimension': config.get('dimension', 'height')
        }

        func = state['function']
        results = func(detections_a, [], function_config)  # detections_b 为空列表

        # 收集所有匹配的检测框
        all_detections = []
        for r in results:
            all_detections.append(r['object_a'])

    else:
        # 双输入函数：需要 detections_a 和 detections_b
        input_b_config = config.get('input_b', {})
        node_b_id = input_b_config.get('node_id')

        if not node_b_id:
            logger.warning(f"[函数计算器] 双输入函数缺少输入节点B配置")
            return _empty_result()

        result_b = upstream_results.get(node_b_id, {})
        detections_b = result_b.get('detections', [])

        class_filter_b = input_b_config.get('class_filter', [])
        if class_filter_b:
            detections_b = [d for d in detections_b if d.get('class') in class_filter_b]

        if not detections_b:
            return _empty_result()

        function_config = {
            'threshold': config.get('threshold', 0.7),
            'operator': config.get('operator', 'less_than')
        }

        func = state['function']
        results = func(detections_a, detections_b, function_config)

        # 收集所有匹配的检测框
        all_detections = []
        for r in results:
            all_detections.append(r['object_a'])
            all_detections.append(r['object_b'])

    result = build_result(
        all_detections,
        metadata={
            'function_name': state['function_name'],
            'matched_count': len(results),
            'is_single_input': is_single_input
        }
    )
    result['function_results'] = results
    return result


def cleanup(state: dict) -> None:
    pass
