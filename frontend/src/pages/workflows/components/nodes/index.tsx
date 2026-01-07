import VideoSourceNode from './VideoSourceNode';
import AlgorithmNode from './AlgorithmNode';
import ConditionNode from './ConditionNode';
import ROINode from './ROINode';
import AlertNode from './AlertNode';

// 直接导出 nodeTypes 对象，避免每次渲染创建新对象
export const nodeTypes = {
  videoSource: VideoSourceNode,
  algorithm: AlgorithmNode,
  condition: ConditionNode,
  roi: ROINode,
  alert: AlertNode,
};

// 保留函数以向后兼容（已废弃）
export const getNodeTypes = () => nodeTypes;

export {
  VideoSourceNode,
  AlgorithmNode,
  ConditionNode,
  ROINode,
  AlertNode,
};
