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
    condition_kind: str = "count"
    target_count: int = 1
    """数量阈值，默认为1"""

    comparison_type: str = ">="
    """比较类型: '>=' (至少N个) 或 '==' (正好N个)"""

    source_node_id: Optional[str] = None
    text_operator: str = "contains"
    pattern_type: str = "keywords"
    keywords: List[str] = field(default_factory=list)
    keyword_logic: str = "any"
    regex_pattern: str = ""
    case_sensitive: bool = False
    window_size: int = 10
    direction: str = "both"
    relative_threshold: float = 0.5
    absolute_threshold: int = 3
    confirmation_count: int = 1
    labels: List[str] = field(default_factory=list)


@dataclass
class TimeScheduleNodeData(NodeContext):
    node_type: str = "time_schedule"
    weekly_schedule: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)

@dataclass
class RoiDrawNodeData(NodeContext):
    node_type: str = "roi_draw"
    roi_regions: Optional[List[Dict[str, Any]]] = None
    """
    ROI区域配置列表，每个区域包含：
    - name: 区域名称（如：大门、停车场）
    - mode: 检测模式 ("pre_mask"、"crop_infer" 或 "post_filter")
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
        "mode": "crop_infer",
        "polygon": [{"x": 0.5, "y": 0.6}, {"x": 0.7, "y": 0.8}, ...]
      }
    ]
    """


@dataclass
class AlertNodeData(NodeContext):
    node_type: str = "alert"
    alert_level: Optional[str] = None
    """告警级别: info, warning, error, critical"""

    alert_message: Optional[str] = None
    """告警消息模板"""

    alert_type: Optional[str] = None
    """告警类型（用于区分不同类型的告警，如 'person', 'vehicle' 等）"""

    trigger_condition: Optional[Dict[str, Any]] = None
    """
    触发条件配置（窗口检测）
    {
        "enable": bool,                # 是否启用窗口检测
        "window_size": int,            # 时间窗口（秒）
        "mode": "ratio" | "consecutive" | "count",  # 检测模式
        "threshold": float             # 阈值（比例0-1 或 次数）
    }
    """

    suppression: Optional[Dict[str, Any]] = None
    """
    告警抑制配置（触发后的冷却期）
    {
        "enable": bool,                # 是否启用抑制
        "seconds": int                 # 抑制时长（秒）
    }
    """

    vl_validation: Optional[Dict[str, Any]] = None
    """
    VL核验配置
    {
        "enable": bool,
        "prompt_template": str
    }
    """

    message_format: Optional[str] = None
    """
    消息格式类型（用于 ExecutionLogCollector）
    - 'detailed': 详细格式（包含节点ID和级别）
    - 'simple': 简单格式（仅消息内容）
    - 'summary': 汇总格式（按级别分组）
    """

    publish_to_mq: bool = True
    """
    是否将该节点的告警输出到当前消息队列提供方。
    仅在告警真正触发（通过触发条件、抑制期、VL 核验之后）时生效。
    默认 True；关闭后该节点告警不会推送 MQ（全局开关及提供方由系统设置控制）。
    """

OutputNodeData = AlertNodeData  # Output节点与Alert节点配置相同

@dataclass
class FunctionNodeData(NodeContext):
    node_type: str = "function"
    data_id: Optional[int] = None
    interval_seconds: Optional[float] = None
    config: Optional[Dict[str, Any]] = None
    input_nodes: Optional[List[str]] = None


@dataclass
class ExternalApiNodeData(NodeContext):
    node_type: str = "external_api"
    data_id: Optional[int] = None
    interval_seconds: Optional[float] = None
    config: Optional[Dict[str, Any]] = None


@dataclass
class WebhookNodeData(NodeContext):
    node_type: str = "webhook"
    config: Optional[Dict[str, Any]] = None


def create_node_data(node_dict: Dict) -> NodeContext:
    node_type = node_dict.get('type')
    node_id = node_dict.get('id')
    data = node_dict.get('data', {})

    node_classes: Dict[str, Type[NodeContext]] = {
        'source': SourceNodeData,
        'algorithm': AlgorithmNodeData,
        'condition': ConditionNodeData,
        'time_schedule': TimeScheduleNodeData,
        'output': OutputNodeData,
        'roi_draw': RoiDrawNodeData,
        'roi': RoiDrawNodeData,
        'alert': AlertNodeData,
        'function': FunctionNodeData,
        'external_api': ExternalApiNodeData,
        'webhook': WebhookNodeData,
    }

    node_class = node_classes.get(node_type)
    if not node_class:
        raise ValueError(f"Unknown node type: {node_type}")

    data_id_raw = node_dict.get('dataId') or data.get('dataId')
    data_id = int(data_id_raw) if data_id_raw is not None else None

    if node_type == 'algorithm':
        config = node_dict.get('config', {})
        return node_class(
            node_type=node_type,
            node_id=node_id,
            data_id=data_id,
            interval_seconds=config.get('interval_seconds'),
            config=config
        )
    elif node_type == 'function':
        config = node_dict.get('config', {})
        return node_class(
            node_type=node_type,
            node_id=node_id,
            data_id=data_id,
            interval_seconds=config.get('interval_seconds'),
            config=config,
            input_nodes=data.get('input_nodes', [])
        )
    elif node_type == 'external_api':
        config = node_dict.get('config', {})
        return node_class(
            node_type=node_type,
            node_id=node_id,
            data_id=data_id,
            interval_seconds=config.get('interval_seconds'),
            config=config
        )
    elif node_type == 'webhook':
        return node_class(
            node_type=node_type,
            node_id=node_id,
            config=node_dict.get('config', {}) or {}
        )
    elif node_type == 'source':
        return node_class(
            node_type=node_type,
            node_id=node_id,
            data_id=data_id
        )
    elif node_type == 'condition':
        # Condition 节点读取配置（支持驼峰和蛇形两种命名）
        target_count = data.get('targetCount') or data.get('target_count', 1)
        comparison_type = data.get('comparisonType') or data.get('comparison_type', '>=')
        condition_kind = data.get('conditionKind') or data.get('condition_kind', 'count')

        return node_class(
            node_type=node_type,
            node_id=node_id,
            condition_kind=condition_kind,
            target_count=target_count,
            comparison_type=comparison_type,
            source_node_id=data.get('sourceNodeId') or data.get('source_node_id'),
            text_operator=data.get('textOperator') or data.get('text_operator', 'contains'),
            pattern_type=data.get('patternType') or data.get('pattern_type', 'keywords'),
            keywords=data.get('keywords') if isinstance(data.get('keywords'), list) else [],
            keyword_logic=data.get('keywordLogic') or data.get('keyword_logic', 'any'),
            regex_pattern=data.get('regexPattern') or data.get('regex_pattern', ''),
            case_sensitive=bool(data.get('caseSensitive') if 'caseSensitive' in data else data.get('case_sensitive', False)),
            window_size=int(data.get('windowSize', data.get('window_size', 10))),
            direction=data.get('direction', 'both'),
            relative_threshold=float(data.get('relativeThreshold', data.get('relative_threshold', 0.5))),
            absolute_threshold=int(data.get('absoluteThreshold', data.get('absolute_threshold', 3))),
            confirmation_count=int(data.get('confirmationCount', data.get('confirmation_count', 1))),
            labels=[str(item).strip() for item in data.get('labels', []) if str(item).strip()]
            if isinstance(data.get('labels'), list) else [],
        )
    elif node_type == 'time_schedule':
        return node_class(
            node_type=node_type,
            node_id=node_id,
            weekly_schedule=data.get('weeklySchedule') or data.get('weekly_schedule') or {},
        )
    elif node_type in ('roi_draw', 'roi'):  # 支持前后端两种类型名称
        # 从 data 读取新的数据格式（支持驼峰和蛇形两种命名）
        roi_regions = data.get('roiRegions') or data.get('roi_regions', [])

        return node_class(
            node_type=node_type,
            node_id=node_id,
            roi_regions=roi_regions
        )
    elif node_type == 'alert':
        # Alert 节点读取配置（支持驼峰和蛇形两种命名）
        alert_level = data.get('alertLevel') or data.get('alert_level')
        alert_message = data.get('alertMessage') or data.get('alert_message')
        alert_type = data.get('alertType') or data.get('alert_type')
        message_format = data.get('messageFormat') or data.get('message_format')
        trigger_condition = data.get('triggerCondition') or data.get('trigger_condition')
        suppression = data.get('suppression')
        vl_validation = data.get('vlValidation') or data.get('vl_validation')
        # 布尔字段不能用 `or` 串联（False 会被覆盖），需显式回退到默认值 True
        publish_to_mq = data.get('publishToMq')
        if publish_to_mq is None:
            publish_to_mq = data.get('publish_to_mq')
        if publish_to_mq is None:
            publish_to_mq = True

        return node_class(
            node_type=node_type,
            node_id=node_id,
            alert_level=alert_level,
            alert_message=alert_message,
            alert_type=alert_type,
            message_format=message_format,
            trigger_condition=trigger_condition,
            suppression=suppression,
            vl_validation=vl_validation,
            publish_to_mq=publish_to_mq
        )
    else:
        return node_class(
            node_type=node_type,
            node_id=node_id
        )
