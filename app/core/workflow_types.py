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


@dataclass
class ConditionNodeData(NodeContext):
    node_type: str = "condition"


@dataclass
class OutputNodeData(NodeContext):
    node_type: str = "output"


def create_node_data(node_dict: Dict) -> NodeContext:
    node_type = node_dict.get('type')
    node_id = node_dict.get('id')
    data = node_dict.get('data', {})

    node_classes: Dict[str, Type[NodeContext]] = {
        'source': SourceNodeData,
        'algorithm': AlgorithmNodeData,
        'condition': ConditionNodeData,
        'output': OutputNodeData,
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
            interval_seconds=data.get('interval_seconds')
        )
    elif node_type == 'source':
        return node_class(
            node_type=node_type,
            node_id=node_id,
            data_id=data_id
        )
    else:
        return node_class(
            node_type=node_type,
            node_id=node_id
        )

