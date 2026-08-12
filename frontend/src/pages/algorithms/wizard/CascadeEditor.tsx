import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Card,
  Col,
  Collapse,
  Drawer,
  Form,
  Image,
  Input,
  InputNumber,
  Radio,
  Row,
  Select,
  Space,
  Tag,
  Upload,
  message,
} from 'antd';
import type { UploadFile } from 'antd/es/upload/interface';
import {
  ApartmentOutlined,
  BranchesOutlined,
  CloseOutlined,
  DeleteOutlined,
  ExperimentOutlined,
  InboxOutlined,
  NodeIndexOutlined,
  PlusOutlined,
  RadarChartOutlined,
  RedoOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import ReactFlow, {
  Background,
  BackgroundVariant,
  Connection,
  Controls,
  Edge,
  Handle,
  MarkerType,
  MiniMap,
  Node,
  NodeProps,
  Position,
  ReactFlowInstance,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  useEdgesState,
  useNodesState,
} from 'reactflow';
import 'reactflow/dist/style.css';
import Button from '@/components/common/AppButton';
import { previewCascadeAlgorithm } from '@/services/api';

type NodeKind = 'frame' | 'detector' | 'predicate' | 'logic' | 'output';
type EdgeKind = 'data' | 'rule';
type PredicateOperator = 'exists' | 'not_exists' | 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte';
type LogicOperator = 'and' | 'or' | 'not';

interface InferenceConfig {
  backend: string;
  nms_iou: number;
  [key: string]: unknown;
}

export interface CascadeGraphNode {
  id: string;
  type: NodeKind;
  name: string;
  model_id?: number | null;
  class_ids?: number[];
  confidence?: number;
  max_candidates?: number;
  expand_ratio?: number;
  inference?: InferenceConfig;
  operator?: PredicateOperator | LogicOperator;
  value?: number;
  label?: string;
  color?: string;
  box_source_node_id?: string | null;
}

export interface CascadeGraphEdge {
  id?: string;
  source: string;
  target: string;
  kind: EdgeKind;
}

export interface CascadeConfig {
  version: 2;
  evaluation: {
    scope: 'per_anchor' | 'frame';
    anchor_node_id: string | null;
  };
  nodes: CascadeGraphNode[];
  edges: CascadeGraphEdge[];
  layout: {
    nodes: Record<string, { x: number; y: number }>;
  };
}

interface LegacyCascadeConfig {
  version?: 1;
  stages?: Array<{
    id: string;
    name: string;
    model_id: number | null;
    class_ids?: number[];
    confidence?: number;
    max_candidates?: number;
    inference?: InferenceConfig;
    input?: { expand_ratio?: number };
  }>;
  output?: { label?: string; color?: string };
}

interface ModelOption {
  id: number;
  name: string;
  enabled: boolean;
  model_type: string;
  framework: string;
  version?: string;
  classes?: Record<string, string>;
}

interface CascadeEditorProps {
  models: ModelOption[];
  value: CascadeConfig;
  onChange: (value: CascadeConfig) => void;
}

interface StagePreview {
  stage_id?: string;
  node_id?: string;
  stage_name?: string;
  node_name?: string;
  status: string;
  execution_state?: 'matched' | 'not_matched' | 'skipped' | 'blocked' | 'failed' | 'degraded';
  reason_code?: string;
  reason?: string;
  upstream_node_id?: string;
  upstream_node_name?: string;
  input_kind?: 'frame' | 'crops';
  input_count: number;
  successful_inferences?: number;
  failed_inferences?: number;
  detection_count: number;
  forwarded_count?: number;
  pruned_count?: number;
  error_count: number;
  errors?: string[];
  inference_time_ms: number;
  image: string;
}

interface ContextEvaluation {
  anchor_record_id: number | null;
  anchor_box: number[] | null;
  state: 'true' | 'false' | 'unknown';
  predicates: Array<{
    node_id: string;
    name: string;
    operator: string;
    value?: number;
    count: number;
    state: 'true' | 'false' | 'unknown';
    source_node_id?: string;
    source_node_name?: string;
    reason?: string;
  }>;
  summary?: string;
  rules?: Array<{
    node_id: string;
    name: string;
    node_type: 'predicate' | 'logic';
    operator: string;
    state: 'true' | 'false' | 'unknown';
  }>;
}

interface PreviewResult {
  success: boolean;
  error?: string;
  detection_count: number;
  result_image?: string;
  node_previews?: StagePreview[];
  stage_previews?: StagePreview[];
  context_evaluations?: ContextEvaluation[];
  diagnosis?: {
    state: 'matched' | 'not_matched' | 'unknown' | 'no_context';
    summary: string;
    first_break_node_id?: string | null;
    first_break_node_name?: string | null;
  };
}

interface CanvasNodeData {
  graphNode: CascadeGraphNode;
  selected: boolean;
  status?: StagePreview;
}

const SUPPORTED_MODEL_TYPES = new Set(['YOLO', 'ONNX', 'RKNN']);
const DATA_EDGE_COLOR = '#2563eb';
const RULE_EDGE_COLOR = '#d97706';

const DEFAULT_POSITIONS: Record<NodeKind, { x: number; y: number }> = {
  frame: { x: 40, y: 140 },
  detector: { x: 270, y: 140 },
  predicate: { x: 520, y: 80 },
  logic: { x: 750, y: 220 },
  output: { x: 980, y: 220 },
};

const newId = (prefix: string) => `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;

const detectorNode = (id: string, name: string): CascadeGraphNode => ({
  id,
  type: 'detector',
  name,
  model_id: null,
  class_ids: [],
  confidence: 0.6,
  max_candidates: 20,
  expand_ratio: 0.1,
  inference: { backend: 'auto', nms_iou: 0.45 },
});

export const createEmptyCascadeConfig = (): CascadeConfig => ({
  version: 2,
  evaluation: { scope: 'frame', anchor_node_id: null },
  nodes: [
    { id: 'frame', type: 'frame', name: '画面输入' },
    {
      id: 'output', type: 'output', name: '最终输出', label: '组合事件',
      color: '#ff4d4f', box_source_node_id: null,
    },
  ],
  edges: [],
  layout: {
    nodes: {
      frame: { x: 80, y: 180 },
      output: { x: 760, y: 180 },
    },
  },
});

export const createHelmetTemplate = (): CascadeConfig => ({
  version: 2,
  evaluation: { scope: 'per_anchor', anchor_node_id: 'head' },
  nodes: [
    { id: 'frame', type: 'frame', name: '画面输入' },
    detectorNode('head', '检测头部'),
    detectorNode('helmet', '检测安全帽'),
    { id: 'head_exists', type: 'predicate', name: '检测到头部', operator: 'exists' },
    { id: 'helmet_missing', type: 'predicate', name: '没有安全帽', operator: 'not_exists' },
    { id: 'all', type: 'logic', name: '全部满足', operator: 'and' },
    {
      id: 'output', type: 'output', name: '最终输出', label: '未戴安全帽',
      color: '#ff4d4f', box_source_node_id: 'head',
    },
  ],
  edges: [
    { source: 'frame', target: 'head', kind: 'data' },
    { source: 'head', target: 'helmet', kind: 'data' },
    { source: 'head', target: 'head_exists', kind: 'rule' },
    { source: 'helmet', target: 'helmet_missing', kind: 'rule' },
    { source: 'head_exists', target: 'all', kind: 'rule' },
    { source: 'helmet_missing', target: 'all', kind: 'rule' },
    { source: 'all', target: 'output', kind: 'rule' },
  ],
  layout: {
    nodes: {
      frame: { x: 30, y: 170 }, head: { x: 250, y: 170 }, helmet: { x: 480, y: 170 },
      head_exists: { x: 500, y: 20 }, helmet_missing: { x: 710, y: 170 },
      all: { x: 920, y: 80 }, output: { x: 1140, y: 80 },
    },
  },
});

export const createLinearTemplate = (): CascadeConfig => {
  return {
    version: 2,
    evaluation: { scope: 'per_anchor', anchor_node_id: 'primary' },
    nodes: [
      { id: 'frame', type: 'frame', name: '画面输入' },
      detectorNode('primary', '检测主体'),
      detectorNode('secondary', '检测目标'),
      { id: 'primary_exists', type: 'predicate', name: '检测到主体', operator: 'exists' },
      { id: 'secondary_exists', type: 'predicate', name: '检测到目标', operator: 'exists' },
      { id: 'all', type: 'logic', name: '全部满足', operator: 'and' },
      {
        id: 'output', type: 'output', name: '最终输出', label: '复合事件',
        color: '#ff4d4f', box_source_node_id: 'primary',
      },
    ],
    edges: [
      { source: 'frame', target: 'primary', kind: 'data' },
      { source: 'primary', target: 'secondary', kind: 'data' },
      { source: 'primary', target: 'primary_exists', kind: 'rule' },
      { source: 'secondary', target: 'secondary_exists', kind: 'rule' },
      { source: 'primary_exists', target: 'all', kind: 'rule' },
      { source: 'secondary_exists', target: 'all', kind: 'rule' },
      { source: 'all', target: 'output', kind: 'rule' },
    ],
    layout: {
      nodes: {
        frame: { x: 30, y: 170 }, primary: { x: 250, y: 170 }, secondary: { x: 480, y: 170 },
        primary_exists: { x: 500, y: 20 }, secondary_exists: { x: 710, y: 170 },
        all: { x: 920, y: 80 }, output: { x: 1140, y: 80 },
      },
    },
  };
};

export const normalizeCascadeForEditor = (raw: CascadeConfig | LegacyCascadeConfig): CascadeConfig => {
  if ((raw as CascadeConfig)?.version === 2 && Array.isArray((raw as CascadeConfig).nodes)) {
    const graph = raw as CascadeConfig;
    return {
      ...graph,
      layout: { nodes: { ...(graph.layout?.nodes || {}) } },
      nodes: graph.nodes.map(node => ({ ...node })),
      edges: graph.edges.map(edge => ({ ...edge })),
    };
  }
  const legacy = raw as LegacyCascadeConfig;
  const stages = legacy.stages || [];
  const nodes: CascadeGraphNode[] = [{ id: 'frame', type: 'frame', name: '画面输入' }];
  const edges: CascadeGraphEdge[] = [];
  const positions: Record<string, { x: number; y: number }> = { frame: { x: 30, y: 120 } };
  let previous = 'frame';
  const predicates: string[] = [];
  stages.forEach((stage, index) => {
    nodes.push({
      ...detectorNode(stage.id, stage.name),
      model_id: stage.model_id,
      class_ids: stage.class_ids || [],
      confidence: stage.confidence ?? 0.6,
      max_candidates: stage.max_candidates ?? 20,
      expand_ratio: stage.input?.expand_ratio ?? 0.1,
      inference: stage.inference || { backend: 'auto', nms_iou: 0.45 },
    });
    const predicateId = `${stage.id}_exists`;
    nodes.push({ id: predicateId, type: 'predicate', name: `${stage.name}存在`, operator: 'exists' });
    edges.push(
      { source: previous, target: stage.id, kind: 'data' },
      { source: stage.id, target: predicateId, kind: 'rule' },
    );
    predicates.push(predicateId);
    positions[stage.id] = { x: 250 + index * 240, y: 100 };
    positions[predicateId] = { x: 250 + index * 240, y: 300 };
    previous = stage.id;
  });
  nodes.push(
    { id: 'all_stages', type: 'logic', name: '全部阶段命中', operator: 'and' },
    {
      id: 'output', type: 'output', name: '最终输出',
      label: legacy.output?.label || '复合事件', color: legacy.output?.color || '#ff4d4f',
      box_source_node_id: stages[0]?.id || null,
    },
  );
  predicates.forEach(source => edges.push({ source, target: 'all_stages', kind: 'rule' }));
  edges.push({ source: 'all_stages', target: 'output', kind: 'rule' });
  positions.all_stages = { x: 600, y: 480 };
  positions.output = { x: 850, y: 480 };
  return {
    version: 2,
    evaluation: { scope: 'per_anchor', anchor_node_id: stages[0]?.id || null },
    nodes,
    edges,
    layout: { nodes: positions },
  };
};

export const getCascadeOutput = (config: CascadeConfig) => (
  config.nodes.find(node => node.type === 'output')
);

export const validateCascadeGraph = (config: CascadeConfig): string | null => {
  const frames = config.nodes.filter(node => node.type === 'frame');
  const detectors = config.nodes.filter(node => node.type === 'detector');
  const outputs = config.nodes.filter(node => node.type === 'output');
  if (frames.length !== 1) return '组合检测必须保留一个画面输入节点';
  if (detectors.length < 1) return '请至少添加一个检测节点';
  if (detectors.length > 8) return '检测节点最多支持 8 个';
  if (outputs.length !== 1) return '组合检测必须保留一个最终输出节点';
  for (const detector of detectors) {
    if (!detector.name.trim()) return '请填写检测节点名称';
    if (!detector.model_id) return `请为“${detector.name}”选择模型`;
    const inputs = config.edges.filter(edge => edge.kind === 'data' && edge.target === detector.id);
    if (inputs.length !== 1) return `检测节点“${detector.name}”需要一个蓝色数据输入`;
  }
  for (const predicate of config.nodes.filter(node => node.type === 'predicate')) {
    const inputs = config.edges.filter(edge => edge.kind === 'rule' && edge.target === predicate.id);
    if (inputs.length !== 1) return `条件“${predicate.name}”需要连接一个检测节点`;
  }
  for (const logic of config.nodes.filter(node => node.type === 'logic')) {
    const inputs = config.edges.filter(edge => edge.kind === 'rule' && edge.target === logic.id);
    if (logic.operator === 'not' ? inputs.length !== 1 : inputs.length < 2) {
      return `${logic.name}的判定输入数量不正确`;
    }
  }
  const output = outputs[0];
  if (!output.label?.trim()) return '请填写最终输出标签';
  if (config.edges.filter(edge => edge.kind === 'rule' && edge.target === output.id).length !== 1) {
    return '最终输出需要连接一个橙色判定输入';
  }
  const reverseRuleInputs = new Map<string, string[]>();
  config.edges.filter(edge => edge.kind === 'rule').forEach(edge => {
    reverseRuleInputs.set(edge.target, [...(reverseRuleInputs.get(edge.target) || []), edge.source]);
  });
  const connectedToOutput = new Set<string>();
  const pending = [output.id];
  while (pending.length) {
    const nodeId = pending.pop()!;
    if (connectedToOutput.has(nodeId)) continue;
    connectedToOutput.add(nodeId);
    pending.push(...(reverseRuleInputs.get(nodeId) || []));
  }
  const disconnectedRule = config.nodes.find(node => ['predicate', 'logic'].includes(node.type) && !connectedToOutput.has(node.id));
  if (disconnectedRule) return `判定节点“${disconnectedRule.name}”尚未连接到最终输出`;
  if (config.evaluation.scope === 'per_anchor' && !detectors.some(node => node.id === config.evaluation.anchor_node_id)) {
    return '逐主体判定需要选择锚点检测节点';
  }
  if (output.box_source_node_id && !detectors.some(node => node.id === output.box_source_node_id)) {
    return '最终输出的画框来源已不存在，请重新选择';
  }
  for (const kind of ['data', 'rule'] as EdgeKind[]) {
    const adjacency = new Map<string, string[]>();
    config.edges.filter(edge => edge.kind === kind).forEach(edge => {
      adjacency.set(edge.source, [...(adjacency.get(edge.source) || []), edge.target]);
    });
    const visiting = new Set<string>();
    const visited = new Set<string>();
    const hasCycle = (nodeId: string): boolean => {
      if (visiting.has(nodeId)) return true;
      if (visited.has(nodeId)) return false;
      visiting.add(nodeId);
      if ((adjacency.get(nodeId) || []).some(hasCycle)) return true;
      visiting.delete(nodeId);
      visited.add(nodeId);
      return false;
    };
    if (config.nodes.some(node => hasCycle(node.id))) {
      return kind === 'data' ? '蓝色检测数据流不能形成循环' : '橙色判定流不能形成循环';
    }
  }
  return null;
};

const kindLabel: Record<NodeKind, string> = {
  frame: '画面', detector: '检测', predicate: '条件', logic: '逻辑', output: '输出',
};

const predicateLabel: Record<PredicateOperator, string> = {
  exists: '存在', not_exists: '不存在', eq: '数量 =', ne: '数量 ≠',
  gt: '数量 >', gte: '数量 ≥', lt: '数量 <', lte: '数量 ≤',
};

const logicLabel: Record<LogicOperator, string> = { and: '全部满足', or: '任一满足', not: '取反' };

const executionStateMeta: Record<string, { label: string; color: string }> = {
  matched: { label: '已命中', color: 'success' },
  not_matched: { label: '已执行·未命中', color: 'default' },
  skipped: { label: '上游无目标·未执行', color: 'default' },
  blocked: { label: '上游异常·未执行', color: 'warning' },
  failed: { label: '执行失败', color: 'error' },
  degraded: { label: '部分失败', color: 'warning' },
};

const truthStateMeta = {
  true: { label: '成立', color: 'success' },
  false: { label: '不成立', color: 'default' },
  unknown: { label: '未知', color: 'warning' },
} as const;

const GraphNodeCard = memo(({ data }: NodeProps<CanvasNodeData>) => {
  const node = data.graphNode;
  const status = data.status;
  const executionMeta = status?.execution_state ? executionStateMeta[status.execution_state] : null;
  const subtitle = node.type === 'detector'
    ? `模型 ${node.model_id ? `#${node.model_id}` : '未选择'} · 阈值 ${node.confidence ?? 0.6}`
    : node.type === 'predicate'
      ? `${predicateLabel[node.operator as PredicateOperator] || '检测条件'}${node.value !== undefined ? ` ${node.value}` : ''}`
      : node.type === 'logic'
        ? logicLabel[node.operator as LogicOperator]
        : node.type === 'output'
          ? node.label || '未配置标签'
          : '完整视频帧';
  return (
    <div className={`combination-node combination-node-${node.type} ${data.selected ? 'is-selected' : ''} ${status ? `status-${status.status}` : ''}`}>
      {node.type === 'detector' ? <Handle type="target" position={Position.Left} id="data-in" className="data-handle" /> : null}
      {node.type === 'predicate' || node.type === 'logic' || node.type === 'output'
        ? <Handle type="target" position={Position.Top} id="rule-in" className="rule-handle" /> : null}
      <div className="combination-node-kicker">
        <span>{kindLabel[node.type]}</span>
        {status ? <Tag color={executionMeta?.color || (status.status === 'ok' ? 'success' : status.status === 'failed' ? 'error' : 'warning')}>{executionMeta?.label || status.status}</Tag> : null}
      </div>
      <strong>{node.name}</strong>
      <small>{subtitle}</small>
      {status ? (
        <div className="combination-node-metrics">
          <span>输入 {status.input_count}</span>
          <span>命中 {status.detection_count}</span>
          <span>{status.inference_time_ms} ms</span>
        </div>
      ) : null}
      {node.type === 'frame' || node.type === 'detector'
        ? <Handle type="source" position={Position.Right} id="data-out" className="data-handle" /> : null}
      {node.type === 'detector' || node.type === 'predicate' || node.type === 'logic'
        ? <Handle type="source" position={Position.Bottom} id="rule-out" className="rule-handle" /> : null}
    </div>
  );
});

const nodeTypes = { graphNode: GraphNodeCard };

const edgeStyle = (kind: EdgeKind): Partial<Edge> => ({
  animated: kind === 'data',
  style: {
    stroke: kind === 'data' ? DATA_EDGE_COLOR : RULE_EDGE_COLOR,
    strokeWidth: 2.2,
    strokeDasharray: kind === 'rule' ? '7 5' : undefined,
  },
  markerEnd: {
    type: MarkerType.ArrowClosed,
    color: kind === 'data' ? DATA_EDGE_COLOR : RULE_EDGE_COLOR,
  },
});

const CascadeEditor: React.FC<CascadeEditorProps> = ({ models, value, onChange }) => {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [resultOpen, setResultOpen] = useState(false);
  const historyRef = useRef<CascadeConfig[]>([value]);
  const historyIndexRef = useRef(0);
  const [, setHistoryVersion] = useState(0);
  const reactFlowRef = useRef<ReactFlowInstance | null>(null);

  const compatibleModels = useMemo(
    () => models.filter(model => model.enabled && SUPPORTED_MODEL_TYPES.has(model.model_type?.toUpperCase())),
    [models],
  );
  const modelById = useMemo(() => new Map(compatibleModels.map(model => [model.id, model])), [compatibleModels]);
  const previewByNode = useMemo(() => new Map(
    (preview?.node_previews || preview?.stage_previews || []).map(item => [item.node_id || item.stage_id || '', item]),
  ), [preview]);

  const toFlowNodes = useCallback((config: CascadeConfig): Node<CanvasNodeData>[] => config.nodes.map((node, index) => ({
    id: node.id,
    type: 'graphNode',
    position: config.layout?.nodes?.[node.id] || {
      x: DEFAULT_POSITIONS[node.type].x + (index % 3) * 30,
      y: DEFAULT_POSITIONS[node.type].y + Math.floor(index / 3) * 130,
    },
    deletable: node.type !== 'frame' && node.type !== 'output',
    data: { graphNode: node, selected: node.id === selectedNodeId, status: previewByNode.get(node.id) },
  })), [previewByNode, selectedNodeId]);
  const toFlowEdges = useCallback((config: CascadeConfig): Edge[] => config.edges.map(edge => ({
    id: edge.id || `${edge.kind}_${edge.source}_${edge.target}`,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.kind === 'data' ? 'data-out' : 'rule-out',
    targetHandle: edge.kind === 'data' ? 'data-in' : 'rule-in',
    data: { kind: edge.kind },
    ...edgeStyle(edge.kind),
  })), []);

  const [flowNodes, setFlowNodes, handleFlowNodesChange] = useNodesState<CanvasNodeData>(toFlowNodes(value));
  const [flowEdges, setFlowEdges, handleFlowEdgesChange] = useEdgesState(toFlowEdges(value));

  useEffect(() => setFlowNodes(toFlowNodes(value)), [setFlowNodes, toFlowNodes, value]);
  useEffect(() => setFlowEdges(toFlowEdges(value)), [setFlowEdges, toFlowEdges, value]);

  const updateConfig = useCallback((next: CascadeConfig) => {
    const history = historyRef.current.slice(0, historyIndexRef.current + 1);
    history.push(next);
    if (history.length > 50) history.shift();
    historyRef.current = history;
    historyIndexRef.current = history.length - 1;
    setHistoryVersion(version => version + 1);
    onChange(next);
    setPreview(null);
  }, [onChange]);

  const travelHistory = useCallback((offset: -1 | 1) => {
    const nextIndex = historyIndexRef.current + offset;
    if (nextIndex < 0 || nextIndex >= historyRef.current.length) return;
    historyIndexRef.current = nextIndex;
    setHistoryVersion(version => version + 1);
    onChange(historyRef.current[nextIndex]);
    setPreview(null);
    setSelectedNodeId(null);
  }, [onChange]);

  const updateNode = useCallback((nodeId: string, patch: Partial<CascadeGraphNode>) => {
    updateConfig({
      ...value,
      nodes: value.nodes.map(node => node.id === nodeId ? { ...node, ...patch } : node),
    });
  }, [updateConfig, value]);

  const removeNode = useCallback((nodeId: string) => {
    const node = value.nodes.find(item => item.id === nodeId);
    if (!node || node.type === 'frame' || node.type === 'output') {
      message.warning('画面输入和最终输出不能删除');
      return;
    }
    updateConfig({
      ...value,
      nodes: value.nodes.filter(item => item.id !== nodeId).map(item => (
        item.type === 'output' && item.box_source_node_id === nodeId
          ? { ...item, box_source_node_id: null }
          : item
      )),
      edges: value.edges.filter(edge => edge.source !== nodeId && edge.target !== nodeId),
      evaluation: value.evaluation.anchor_node_id === nodeId
        ? { ...value.evaluation, anchor_node_id: null }
        : value.evaluation,
    });
    setSelectedNodeId(null);
  }, [updateConfig, value]);

  const addNode = useCallback((type: Exclude<NodeKind, 'frame' | 'output'>) => {
    if (type === 'detector' && value.nodes.filter(node => node.type === 'detector').length >= 8) {
      message.warning('检测节点最多支持 8 个');
      return;
    }
    const id = newId(type);
    const node: CascadeGraphNode = type === 'detector'
      ? detectorNode(id, '新检测目标')
      : type === 'predicate'
        ? { id, type, name: '检测条件', operator: 'exists' }
        : { id, type, name: '组合条件', operator: 'and' };
    const position = reactFlowRef.current?.screenToFlowPosition({ x: 520, y: 360 }) || DEFAULT_POSITIONS[type];
    updateConfig({
      ...value,
      nodes: [...value.nodes, node],
      layout: { nodes: { ...value.layout.nodes, [id]: position } },
    });
    setSelectedNodeId(id);
  }, [updateConfig, value]);

  const connectionKind = (connection: Connection): EdgeKind | null => {
    if (connection.sourceHandle === 'data-out' && connection.targetHandle === 'data-in') return 'data';
    if (connection.sourceHandle === 'rule-out' && connection.targetHandle === 'rule-in') return 'rule';
    return null;
  };

  const isValidConnection = useCallback((connection: Connection) => {
    const kind = connectionKind(connection);
    if (!kind || !connection.source || !connection.target || connection.source === connection.target) return false;
    const source = value.nodes.find(node => node.id === connection.source);
    const target = value.nodes.find(node => node.id === connection.target);
    if (!source || !target) return false;
    if (kind === 'data' && (!(source.type === 'frame' || source.type === 'detector') || target.type !== 'detector')) return false;
    if (kind === 'rule') {
      const valid = source.type === 'detector' && target.type === 'predicate'
        || (source.type === 'predicate' || source.type === 'logic') && (target.type === 'logic' || target.type === 'output');
      if (!valid) return false;
    }
    const incoming = value.edges.filter(edge => edge.kind === kind && edge.target === target.id);
    if (target.type === 'detector' || target.type === 'predicate' || target.type === 'output' || target.operator === 'not') {
      if (incoming.length >= 1) return false;
    }
    const adjacency = new Map<string, string[]>();
    value.edges.filter(edge => edge.kind === kind).forEach(edge => {
      adjacency.set(edge.source, [...(adjacency.get(edge.source) || []), edge.target]);
    });
    const stack = [target.id];
    const visited = new Set<string>();
    while (stack.length) {
      const current = stack.pop()!;
      if (current === source.id) return false;
      if (visited.has(current)) continue;
      visited.add(current);
      stack.push(...(adjacency.get(current) || []));
    }
    return !value.edges.some(edge => edge.kind === kind && edge.source === source.id && edge.target === target.id);
  }, [value]);

  const onConnect = useCallback((connection: Connection) => {
    if (!isValidConnection(connection) || !connection.source || !connection.target) {
      message.warning('端口类型不匹配，或目标节点已有唯一输入');
      return;
    }
    const kind = connectionKind(connection)!;
    updateConfig({
      ...value,
      edges: [...value.edges, { id: `${kind}_${connection.source}_${connection.target}`, source: connection.source, target: connection.target, kind }],
    });
  }, [isValidConnection, updateConfig, value]);

  const onEdgesChange = useCallback((changes: Parameters<typeof applyEdgeChanges>[0]) => {
    const nextFlowEdges = applyEdgeChanges(changes, flowEdges);
    setFlowEdges(nextFlowEdges);
    if (changes.some(change => change.type === 'remove')) {
      const ids = new Set(nextFlowEdges.map(edge => edge.id));
      updateConfig({
        ...value,
        edges: value.edges.filter(edge => ids.has(edge.id || `${edge.kind}_${edge.source}_${edge.target}`)),
      });
    }
  }, [flowEdges, setFlowEdges, updateConfig, value]);

  const onNodesChange = useCallback((changes: Parameters<typeof applyNodeChanges>[0]) => {
    handleFlowNodesChange(changes);
    const removed = changes.find(change => change.type === 'remove');
    if (removed) removeNode(removed.id);
  }, [handleFlowNodesChange, removeNode]);

  const onNodeDragStop = useCallback((_: React.MouseEvent, node: Node) => {
    updateConfig({
      ...value,
      layout: { nodes: { ...value.layout.nodes, [node.id]: node.position } },
    });
  }, [updateConfig, value]);

  const autoLayout = useCallback(() => {
    const typeRows: Record<NodeKind, number> = { frame: 0, detector: 0, predicate: 0, logic: 0, output: 0 };
    const columns: Record<NodeKind, number> = { frame: 0, detector: 1, predicate: 2, logic: 3, output: 4 };
    const positions: Record<string, { x: number; y: number }> = {};
    value.nodes.forEach(node => {
      positions[node.id] = { x: 40 + columns[node.type] * 245, y: 40 + typeRows[node.type] * 145 };
      typeRows[node.type] += 1;
    });
    updateConfig({ ...value, layout: { nodes: positions } });
    window.setTimeout(() => reactFlowRef.current?.fitView({ padding: 0.16 }), 50);
  }, [updateConfig, value]);

  const applyTemplate = useCallback((template: 'helmet' | 'linear') => {
    updateConfig(template === 'helmet' ? createHelmetTemplate() : createLinearTemplate());
    setSelectedNodeId(null);
    window.setTimeout(() => reactFlowRef.current?.fitView({ padding: 0.14 }), 50);
  }, [updateConfig]);

  const runPreview = async () => {
    const validation = validateCascadeGraph(value);
    if (validation) {
      message.warning(validation);
      return;
    }
    const file = fileList[0]?.originFileObj;
    if (!file) {
      message.warning('请先上传测试图片');
      return;
    }
    setPreviewing(true);
    setPreview(null);
    try {
      const result = await previewCascadeAlgorithm(value, file as File);
      setPreview(result);
      setResultOpen(true);
      result.success
        ? message.success(`测试完成，输出 ${result.detection_count} 个业务结果`)
        : message.error(result.error || '测试失败');
    } catch (error: any) {
      const detail = error?.response?.data?.error || error?.message || '测试失败';
      setPreview({ success: false, detection_count: 0, error: detail });
      setResultOpen(true);
      message.error(detail);
    } finally {
      setPreviewing(false);
    }
  };

  const selectedNode = value.nodes.find(node => node.id === selectedNodeId) || null;
  const detectorOptions = value.nodes.filter(node => node.type === 'detector').map(node => ({ value: node.id, label: node.name }));
  const selectedModel = selectedNode?.model_id ? modelById.get(selectedNode.model_id) : undefined;
  const classOptions = Object.entries(selectedModel?.classes || {}).map(([classId, name]) => ({
    value: Number(classId), label: `${name} · ${classId}`,
  }));
  const validationError = validateCascadeGraph(value);
  const hasNegativeRule = value.nodes.some(node => node.type === 'predicate' && node.operator === 'not_exists')
    || value.nodes.some(node => node.type === 'logic' && node.operator === 'not');

  return (
    <div className="cascade-editor combination-editor">
      <div className="combination-toolbar">
        <Space wrap className="combination-template-shortcuts">
          <span className="combination-template-label">参考模板</span>
          <Button onClick={() => applyTemplate('helmet')}>安全帽缺失示例</Button>
          <Button onClick={() => applyTemplate('linear')}>线性级联示例</Button>
        </Space>
        <Space>
          <Button aria-label="撤销" icon={<UndoOutlined />} disabled={historyIndexRef.current === 0} onClick={() => travelHistory(-1)}>撤销</Button>
          <Button aria-label="重做" icon={<RedoOutlined />} disabled={historyIndexRef.current >= historyRef.current.length - 1} onClick={() => travelHistory(1)}>重做</Button>
          <Button icon={<ReloadOutlined />} onClick={autoLayout}>自动布局</Button>
          <Button onClick={() => reactFlowRef.current?.fitView({ padding: 0.16 })}>适应画布</Button>
        </Space>
      </div>

      <div className="combination-scope-bar">
        <div>
          <strong>判定范围</strong>
          <span>{value.evaluation.scope === 'per_anchor' ? '每个主体独立判断，避免目标相互抵消' : '汇总整张画面的检测结果后判断'}</span>
        </div>
        <Radio.Group
          value={value.evaluation.scope}
          optionType="button"
          buttonStyle="solid"
          options={[{ label: '逐主体', value: 'per_anchor' }, { label: '整帧', value: 'frame' }]}
          onChange={event => updateConfig({
            ...value,
            evaluation: {
              scope: event.target.value,
              anchor_node_id: event.target.value === 'per_anchor'
                ? value.evaluation.anchor_node_id || detectorOptions[0]?.value || null
                : null,
            },
          })}
        />
        {value.evaluation.scope === 'per_anchor' ? (
          <Select
            aria-label="逐主体锚点"
            value={value.evaluation.anchor_node_id || undefined}
            placeholder="选择主体节点"
            options={detectorOptions}
            onChange={anchorNodeId => updateConfig({
              ...value, evaluation: { ...value.evaluation, anchor_node_id: anchorNodeId },
            })}
          />
        ) : null}
      </div>

      {validationError ? <Alert type="warning" showIcon message="画布尚未完整" description={validationError} /> : null}
      {hasNegativeRule ? (
        <Alert
          type="info"
          showIcon
          message="反向条件已启用"
          description="只有模型正常执行且返回 0 个目标时，“不存在”才成立；模型超时或故障不会触发告警。建议在下一步启用时间窗口抑制单帧漏检。"
        />
      ) : null}

      <div className="combination-workbench">
        <aside className="combination-palette" aria-label="节点库">
          <div className="combination-palette-heading">
            <span>NODE LIBRARY</span>
            <strong>节点库</strong>
          </div>
          <button type="button" className="combination-palette-item palette-detector" onClick={() => addNode('detector')}>
            <RadarChartOutlined />
            <span><strong>检测节点</strong><small>运行模型并传递目标区域</small></span>
            <PlusOutlined />
          </button>
          <button type="button" className="combination-palette-item palette-predicate" onClick={() => addNode('predicate')}>
            <BranchesOutlined />
            <span><strong>条件节点</strong><small>判断存在、不存在或数量</small></span>
            <PlusOutlined />
          </button>
          <button type="button" className="combination-palette-item palette-logic" onClick={() => addNode('logic')}>
            <NodeIndexOutlined />
            <span><strong>逻辑节点</strong><small>组合 AND、OR、NOT</small></span>
            <PlusOutlined />
          </button>
          <div className="combination-palette-hint">
            <i className="legend-data" />蓝色传递检测区域
            <i className="legend-rule" />橙色传递判定结果
          </div>
        </aside>
        <section className="combination-canvas" aria-label="组合检测画布">
          <div className="combination-legend" aria-label="连线说明">
            <span><i className="legend-data" />数据流</span>
            <span><i className="legend-rule" />判定流</span>
          </div>
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            onInit={instance => { reactFlowRef.current = instance; }}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            onNodeClick={(_, node) => setSelectedNodeId(node.id)}
            onPaneClick={() => setSelectedNodeId(null)}
            onNodeDragStop={onNodeDragStop}
            fitView
            minZoom={0.35}
            maxZoom={1.8}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#cbd5e1" />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              nodeColor={node => {
                const kind = (node.data as CanvasNodeData)?.graphNode.type;
                return kind === 'output' ? '#16a34a' : kind === 'logic' || kind === 'predicate' ? RULE_EDGE_COLOR : DATA_EDGE_COLOR;
              }}
            />
          </ReactFlow>
        </section>

        <aside className="combination-inspector" aria-label="节点属性">
          {selectedNode ? (
            <>
              <div className="combination-inspector-header">
                <div><span>{kindLabel[selectedNode.type]}节点</span><strong>{selectedNode.name}</strong></div>
                <Button size="small" aria-label="关闭属性面板" onClick={() => setSelectedNodeId(null)}><CloseOutlined /></Button>
              </div>
              <Form layout="vertical" className="combination-inspector-form">
                <Form.Item label="节点名称" required>
                  <Input value={selectedNode.name} maxLength={80} onChange={event => updateNode(selectedNode.id, { name: event.target.value })} />
                </Form.Item>

                {selectedNode.type === 'detector' ? (
                  <>
                    <Form.Item label="检测模型" required>
                      <Select
                        showSearch
                        optionFilterProp="label"
                        value={selectedNode.model_id || undefined}
                        placeholder="选择 YOLO 兼容模型"
                        options={compatibleModels.map(model => ({ value: model.id, label: `${model.name} · ${model.model_type}/${model.framework}` }))}
                        onChange={modelId => updateNode(selectedNode.id, { model_id: modelId, class_ids: [] })}
                      />
                    </Form.Item>
                    <Form.Item label="目标类别" extra="留空表示模型的全部类别">
                      <Select
                        mode="tags"
                        value={selectedNode.class_ids || []}
                        options={classOptions}
                        placeholder="全部类别"
                        onChange={items => updateNode(selectedNode.id, {
                          class_ids: items.map(Number).filter(item => Number.isInteger(item) && item >= 0),
                        })}
                      />
                    </Form.Item>
                    <Row gutter={12}>
                      <Col span={12}>
                        <Form.Item label="置信度">
                          <InputNumber min={0} max={1} step={0.05} value={selectedNode.confidence} style={{ width: '100%' }} onChange={number => updateNode(selectedNode.id, { confidence: Number(number ?? 0.6) })} />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        <Form.Item label="区域扩展">
                          <InputNumber min={0} max={1} step={0.05} value={selectedNode.expand_ratio} style={{ width: '100%' }} onChange={number => updateNode(selectedNode.id, { expand_ratio: Number(number ?? 0.1) })} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Collapse
                      ghost
                      size="small"
                      items={[{
                        key: 'advanced',
                        label: '高级推理设置',
                        children: (
                          <>
                            <Form.Item label="推理后端">
                              <Select
                                value={selectedNode.inference?.backend || 'auto'}
                                options={[
                                  { value: 'auto', label: '自动选择' }, { value: 'ultralytics', label: 'Ultralytics' },
                                  { value: 'onnxruntime', label: 'ONNX Runtime' }, { value: 'rknn', label: 'RKNNLite' },
                                ]}
                                onChange={backend => updateNode(selectedNode.id, { inference: { ...(selectedNode.inference || { nms_iou: 0.45 }), backend } })}
                              />
                            </Form.Item>
                            <Row gutter={12}>
                              <Col span={12}><Form.Item label="NMS IOU"><InputNumber min={0} max={1} step={0.05} value={selectedNode.inference?.nms_iou ?? 0.45} style={{ width: '100%' }} onChange={number => updateNode(selectedNode.id, { inference: { ...(selectedNode.inference || { backend: 'auto' }), nms_iou: Number(number ?? 0.45) } })} /></Form.Item></Col>
                              <Col span={12}><Form.Item label="最大候选"><InputNumber min={1} max={200} value={selectedNode.max_candidates || 20} style={{ width: '100%' }} onChange={number => updateNode(selectedNode.id, { max_candidates: Number(number ?? 20) })} /></Form.Item></Col>
                            </Row>
                          </>
                        ),
                      }]}
                    />
                  </>
                ) : null}

                {selectedNode.type === 'predicate' ? (
                  <>
                    <Form.Item label="判断方式">
                      <Select
                        value={selectedNode.operator}
                        options={(Object.entries(predicateLabel) as Array<[PredicateOperator, string]>).map(([value, label]) => ({ value, label }))}
                        onChange={operator => updateNode(selectedNode.id, { operator, value: ['exists', 'not_exists'].includes(operator) ? undefined : selectedNode.value ?? 1 })}
                      />
                    </Form.Item>
                    {!['exists', 'not_exists'].includes(selectedNode.operator || '') ? (
                      <Form.Item label="比较数量"><InputNumber min={0} max={200} value={selectedNode.value ?? 1} style={{ width: '100%' }} onChange={number => updateNode(selectedNode.id, { value: Number(number ?? 1) })} /></Form.Item>
                    ) : null}
                  </>
                ) : null}

                {selectedNode.type === 'logic' ? (
                  <Form.Item label="组合方式">
                    <Select
                      value={selectedNode.operator}
                      options={(Object.entries(logicLabel) as Array<[LogicOperator, string]>).map(([value, label]) => ({ value, label }))}
                      onChange={operator => updateNode(selectedNode.id, { operator })}
                    />
                  </Form.Item>
                ) : null}

                {selectedNode.type === 'output' ? (
                  <>
                    <Form.Item label="输出标签" required><Input value={selectedNode.label} onChange={event => updateNode(selectedNode.id, { label: event.target.value })} /></Form.Item>
                    <Form.Item label="标记颜色"><Input type="color" value={selectedNode.color || '#ff4d4f'} className="combination-color-input" onChange={event => updateNode(selectedNode.id, { color: event.target.value })} /></Form.Item>
                    <Form.Item label="输出框来源" extra="留空时输出无框业务事件">
                      <Select allowClear value={selectedNode.box_source_node_id || undefined} options={detectorOptions} placeholder="仅事件，不画框" onChange={boxSource => updateNode(selectedNode.id, { box_source_node_id: boxSource || null })} />
                    </Form.Item>
                  </>
                ) : null}

                {!['frame', 'output'].includes(selectedNode.type) ? (
                  <Button block tone="danger" icon={<DeleteOutlined />} onClick={() => removeNode(selectedNode.id)}>删除节点</Button>
                ) : null}
              </Form>
            </>
          ) : (
            <div className="combination-inspector-empty">
              <ApartmentOutlined />
              <strong>选择一个节点</strong>
              <p>详细参数会在这里显示。蓝色端口传递画面或目标区域，橙色端口传递判定结果。</p>
            </div>
          )}
        </aside>
      </div>

      <Card title={<Space><ExperimentOutlined />测试当前组合</Space>} className="cascade-preview-card">
        <div className="combination-test-row">
          <Upload.Dragger
            accept="image/*"
            maxCount={1}
            fileList={fileList}
            beforeUpload={() => false}
            onChange={({ fileList: next }) => { setFileList(next.slice(-1)); setPreview(null); }}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">上传真实场景图片</p>
            <p className="ant-upload-hint">测试结果会回写到画布节点，不会保存图片。</p>
          </Upload.Dragger>
          <div className="combination-test-action">
            <ThunderboltOutlined />
            <strong>逐节点解释结果</strong>
            <p>检查每个主体、条件真假、模型异常与最终输出。</p>
            <Button type="primary" icon={<ExperimentOutlined />} loading={previewing} disabled={fileList.length === 0 || Boolean(validationError)} onClick={runPreview}>运行测试</Button>
            {preview ? <Button onClick={() => setResultOpen(true)}>查看上次结果</Button> : null}
          </div>
        </div>
      </Card>

      <Drawer title="组合检测测试结果" open={resultOpen} onClose={() => setResultOpen(false)} width={720}>
        {preview?.error ? <Alert type="error" showIcon message={preview.error} /> : null}
        {preview?.diagnosis ? (
          <Alert
            className="combination-diagnosis"
            type={preview.diagnosis.state === 'matched' ? 'success' : preview.diagnosis.state === 'unknown' ? 'warning' : 'info'}
            showIcon
            message={preview.diagnosis.state === 'matched' ? '组合规则已命中' : preview.diagnosis.state === 'unknown' ? '检测结果不完整' : '组合规则未命中'}
            description={preview.diagnosis.summary}
          />
        ) : null}
        {preview?.context_evaluations?.length ? (
          <Card size="small" title={`主体判定 · ${preview.context_evaluations.length} 个上下文`} className="combination-context-card">
            {preview.context_evaluations.map((context, index) => (
              <div className="combination-context-row" key={context.anchor_record_id ?? index}>
                <Tag color={truthStateMeta[context.state].color}>{truthStateMeta[context.state].label}</Tag>
                <strong>主体 {index + 1}</strong>
                <div className="combination-context-detail">
                  <span>{context.summary || context.predicates.map(item => `${item.name}: ${item.count}`).join(' · ')}</span>
                  {context.predicates.map(item => (
                    <div className="combination-predicate-result" key={item.node_id}>
                      <Tag color={truthStateMeta[item.state].color}>{truthStateMeta[item.state].label}</Tag>
                      <span>{item.reason || `${item.name}：命中 ${item.count} 个`}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </Card>
        ) : null}
        <div className="cascade-preview-results">
          {(preview?.node_previews || preview?.stage_previews || []).map((node, index) => (
            <Card key={node.node_id || node.stage_id || index} size="small" className="cascade-preview-result">
              <Space wrap>
                <strong>{node.node_name || node.stage_name}</strong>
                <Tag color={(node.execution_state && executionStateMeta[node.execution_state]?.color) || (node.status === 'ok' ? 'success' : node.status === 'failed' ? 'error' : 'warning')}>
                  {(node.execution_state && executionStateMeta[node.execution_state]?.label) || node.status}
                </Tag>
                <span>输入 {node.input_count}</span>
                <span>执行 {node.successful_inferences ?? 0}</span>
                <span>命中 {node.detection_count}</span>
                {node.forwarded_count !== undefined ? <span>下传 {node.forwarded_count}</span> : null}
                {node.pruned_count ? <span>截断 {node.pruned_count}</span> : null}
                <span>{node.inference_time_ms} ms</span>
              </Space>
              {node.reason ? <Alert className="cascade-node-reason" type={node.execution_state === 'failed' ? 'error' : node.execution_state === 'blocked' || node.execution_state === 'degraded' ? 'warning' : 'info'} showIcon message={node.reason} /> : null}
              {node.errors?.length ? <div className="cascade-node-errors">{node.errors.map((error, errorIndex) => <div key={`${error}-${errorIndex}`}>{error}</div>)}</div> : null}
              <Image src={node.image} alt={`${node.node_name || node.stage_name}测试结果`} />
            </Card>
          ))}
        </div>
        {preview?.result_image ? <Card size="small" title={`最终结果 · ${preview.detection_count} 个`}><Image src={preview.result_image} alt="组合检测最终结果" /></Card> : null}
      </Drawer>
    </div>
  );
};

export default memo(CascadeEditor);
