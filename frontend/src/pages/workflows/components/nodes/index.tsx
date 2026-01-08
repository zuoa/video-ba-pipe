import VideoSourceNode from './VideoSourceNode';
import AlgorithmNode from './AlgorithmNode';
import ConditionNode from './ConditionNode';
import ROINode from './ROINode';
import AlertNode from './AlertNode';
import FunctionNode from './FunctionNode';

export const nodeTypes = {
  videoSource: VideoSourceNode,
  algorithm: AlgorithmNode,
  condition: ConditionNode,
  roi: ROINode,
  alert: AlertNode,
  function: FunctionNode,
};

export const getNodeTypes = () => nodeTypes;

export {
  VideoSourceNode,
  AlgorithmNode,
  ConditionNode,
  ROINode,
  AlertNode,
  FunctionNode,
};
