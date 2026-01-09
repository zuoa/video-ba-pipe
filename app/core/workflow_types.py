from dataclasses import dataclass, field
from typing import Optional, Any, Dict, List, Type


@dataclass
class NodeInput:
    frame: Any = None
    frame_timestamp: float = 0.0
    has_detection: bool = False
    result: Optional[Dict] = None
    roi_mask: Optional[Any] = None
    node_id: Optional[str] = None


@dataclass
class NodeOutput:
    frame: Any = None
    frame_timestamp: float = 0.0
    has_detection: bool = False
    result: Optional[Dict] = None
    roi_mask: Optional[Any] = None
    node_id: Optional[str] = None


@dataclass
class NodeContext:
    node_type: str = ""
    node_id: str = ""


@dataclass
class SourceNodeData(NodeContext):
    node_type: str = "source"
    data_id: Optional[int] = None


@dataclass
class AlgorithmNodeData(NodeContext):
    node_type: str = "algorithm"
    data_id: Optional[int] = None
    interval_seconds: Optional[float] = None
    config: Optional[Dict[str, Any]] = None


@dataclass
class ConditionNodeData(NodeContext):
    node_type: str = "condition"


@dataclass
class OutputNodeData(NodeContext):
    node_type: str = "output"


@dataclass
class RoiDrawNodeData(NodeContext):
    node_type: str = "roi_draw"
    roi_regions: Optional[List[Dict[str, Any]]] = None
    """
    ROI区域配置列表，每个区域包含：
    - name: 区域名称（如：大门、停车场）
    - mode: 检测模式 ("pre_mask" 或 "post_filter")
    - polygon: 多边形顶点坐标数组 [[x1,y1], [x2,y2], ...]（相对坐标 0-1）

    该节点功能：
    1. 记录热区坐标信息到context['roi_regions']
    2. 不执行实际的图像裁剪操作
    3. 输出roi_regions供下游算法节点使用

    使用示例：
    source -> roi_draw -> algorithm
    algorithm节点会自动使用roi_draw节点配置的roi_regions

    数据格式（从 data.roi_regions 读取）：
    [
      {
        "name": "区域1",
        "mode": "pre_mask",
        "polygon": [{"x": 0.1, "y": 0.2}, {"x": 0.3, "y": 0.4}, ...]
      },
      {
        "name": "区域2",
        "mode": "post_filter",
        "polygon": [{"x": 0.5, "y": 0.6}, {"x": 0.7, "y": 0.8}, ...]
      }
    ]
    """


@dataclass
class AlertNodeData(NodeContext):
    node_type: str = "alert"


@dataclass
class FunctionNodeData(NodeContext):
    node_type: str = "function"
    data_id: Optional[int] = None
    interval_seconds: Optional[float] = None
    config: Optional[Dict[str, Any]] = None
    input_nodes: Optional[List[str]] = None


def create_node_data(node_dict: Dict) -> NodeContext:
    node_type = node_dict.get('type')
    node_id = node_dict.get('id')
    data = node_dict.get('data', {})

    node_classes: Dict[str, Type[NodeContext]] = {
        'source': SourceNodeData,
        'algorithm': AlgorithmNodeData,
        'condition': ConditionNodeData,
        'output': OutputNodeData,
        'roi_draw': RoiDrawNodeData,
        'roi': RoiDrawNodeData,
        'alert': AlertNodeData,
        'function': FunctionNodeData,
    }

    node_class = node_classes.get(node_type)
    if not node_class:
        raise ValueError(f"Unknown node type: {node_type}")

    data_id_raw = node_dict.get('dataId') or data.get('dataId')
    data_id = int(data_id_raw) if data_id_raw is not None else None

    if node_type == 'algorithm':
        return node_class(
            node_type=node_type,
            node_id=node_id,
            data_id=data_id,
            interval_seconds=data.get('interval_seconds'),
            config=data.get('config')
        )
    elif node_type == 'function':
        return node_class(
            node_type=node_type,
            node_id=node_id,
            data_id=data_id,
            interval_seconds=data.get('interval_seconds'),
            config=data.get('config'),
            input_nodes=data.get('input_nodes', [])
        )
    elif node_type == 'source':
        return node_class(
            node_type=node_type,
            node_id=node_id,
            data_id=data_id
        )
    elif node_type in ('roi_draw', 'roi'):  # 支持前后端两种类型名称
        # 从 data 读取新的数据格式（支持驼峰和蛇形两种命名）
        roi_regions = data.get('roiRegions') or data.get('roi_regions', [])

        return node_class(
            node_type=node_type,
            node_id=node_id,
            roi_regions=roi_regions
        )
    else:
        return node_class(
            node_type=node_type,
            node_id=node_id
        )

