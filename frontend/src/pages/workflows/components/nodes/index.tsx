import VideoSourceNode from './VideoSourceNode';
import AlgorithmNode from './AlgorithmNode';
import ConditionNode from './ConditionNode';
import ROINode from './ROINode';
import AlertNode from './AlertNode';
import FunctionNode from './FunctionNode';
import ExternalApiNode from './ExternalApiNode';
import WebhookNode from './WebhookNode';
import TimeScheduleNode from './TimeScheduleNode';
import DetectionFilterNode from './DetectionFilterNode';
import HttpRequestNode from './HttpRequestNode';

export const nodeTypes = {
  videoSource: VideoSourceNode,
  algorithm: AlgorithmNode,
  externalApi: ExternalApiNode,
  httpRequest: HttpRequestNode,
  condition: ConditionNode,
  roi: ROINode,
  alert: AlertNode,
  function: FunctionNode,
  detectionFilter: DetectionFilterNode,
  webhook: WebhookNode,
  timeSchedule: TimeScheduleNode,
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
  TimeScheduleNode,
  DetectionFilterNode,
  HttpRequestNode,
};
