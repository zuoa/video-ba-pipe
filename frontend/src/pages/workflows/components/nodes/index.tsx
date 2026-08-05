import VideoSourceNode from './VideoSourceNode';
import AlgorithmNode from './AlgorithmNode';
import ConditionNode from './ConditionNode';
import ROINode from './ROINode';
import AlertNode from './AlertNode';
import FunctionNode from './FunctionNode';
import ExternalApiNode from './ExternalApiNode';
import WebhookNode from './WebhookNode';

export const nodeTypes = {
  videoSource: VideoSourceNode,
  algorithm: AlgorithmNode,
  externalApi: ExternalApiNode,
  condition: ConditionNode,
  roi: ROINode,
  alert: AlertNode,
  function: FunctionNode,
  webhook: WebhookNode,
};

export const getNodeTypes = () => nodeTypes;

export {
  VideoSourceNode,
  AlgorithmNode,
  ConditionNode,
  ROINode,
  AlertNode,
  FunctionNode,
  ExternalApiNode,
  WebhookNode,
};
