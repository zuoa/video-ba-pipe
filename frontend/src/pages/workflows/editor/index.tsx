import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'umi';
import { Space, message, Spin, Tag } from 'antd';
import Button from '@/components/common/AppButton';
import AppEmptyState from '@/components/common/AppEmptyState';
import {
  CloseOutlined,
  SaveOutlined,
  ExperimentOutlined,
  DeleteOutlined,
  ArrowLeftOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import ReactFlow, {
  Node,
  Edge,
  Background,
  Controls,
  MiniMap,
  addEdge,
  Connection,
  useNodesState,
  useEdgesState,
  NodeTypes,
  BackgroundVariant,
  MarkerType,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { nodeTypes } from '../components/nodes';
import ComponentSidebar from '../components/ComponentSidebar';
import PropertyPanel, { EdgePropertyPanel } from '../components/PropertyPanel';
import TestPanel from '../components/TestPanel';
import { getWorkflow, updateWorkflow, getVideoSources, getVlConfig, getAlgorithms, getExternalApis } from '@/services/api';
import { getAlgorithmDefaultConfidence } from '../utils/algorithmDefaults';
import { createDefaultWeeklySchedule, normalizeWeeklySchedule } from '../utils/timeSchedule';
import '../components/WorkflowEditor.css';

function persistNonConditionEdgeCondition(raw: unknown): 'detected' | 'not_detected' | null {
  if (raw === 'detected' || raw === 'not_detected') {
    return raw;
  }
  return null;
}

function edgeConditionLabel(condition: string | null | undefined): string {
  if (condition === 'detected') return '检测到';
  if (condition === 'not_detected') return '未检测到';
  return '';
}

function isConditionNodeType(node: any): boolean {
  return node?.type === 'condition' || node?.data?.type === 'condition';
}

function getApiErrorMessage(error: any): string | undefined {
  return error?.data?.error || error?.response?.data?.error || error?.message;
}

export default function WorkflowEditorPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const selectedNode = selectedNodeId
    ? nodes.find((node) => node.id === selectedNodeId) || null
    : null;
  const selectedEdge = selectedEdgeId
    ? edges.find((edge) => edge.id === selectedEdgeId) || null
    : null;
  const [rightPanel, setRightPanel] = useState<'properties' | 'test'>('properties');
  const [workflow, setWorkflow] = useState<any>(null);
  const [videoSources, setVideoSources] = useState<any[]>([]);
  const [algorithms, setAlgorithms] = useState<any[]>([]);
  const [externalApis, setExternalApis] = useState<any[]>([]);
  const [vlConfig, setVlConfig] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [resourcesLoaded, setResourcesLoaded] = useState(false);

  // 先加载视频源列表，再加载工作流数据
  useEffect(() => {
    const loadAllData = async () => {
      setLoading(true);
      await Promise.all([
        loadVideoSources(),
        loadAlgorithms(),
        loadExternalApis(),
        loadVlConfig(),
      ]);
      setResourcesLoaded(true);
    };
    loadAllData();
  }, [id]);

  // 视频源加载完成后，加载工作流数据
  useEffect(() => {
    if (resourcesLoaded) {
      loadWorkflowData();
    }
  }, [resourcesLoaded, id]);

  const loadWorkflowData = async () => {
    try {
      setLoading(true);
      const data = await getWorkflow(Number(id));
      setWorkflow(data);

      // 加载节点和连线数据
      const workflowData = data.workflow_data || data.graph_json;

      console.log('加载的工作流数据:', data);
      console.log('workflow_data:', workflowData);

      if (workflowData) {
        try {
          const graphData = typeof workflowData === 'string'
            ? JSON.parse(workflowData)
            : workflowData;

          console.log('解析后的图数据:', graphData);

          // 转换节点数据
          const convertedNodes = (graphData.nodes || []).map((node: any) => {
            const originalType = node.type || node.data?.type;
            const nodeType = originalType === 'source' ? 'videoSource' :
                            originalType === 'algorithm' ? 'algorithm' :
                            originalType === 'external_api' ? 'externalApi' :
                            originalType === 'http_request' ? 'httpRequest' :
                            originalType === 'function' ? 'function' :
                            originalType === 'detection_filter' ? 'detectionFilter' :
                            originalType === 'condition' ? 'condition' :
                            originalType === 'time_schedule' ? 'timeSchedule' :
                            originalType === 'roi' ? 'roi' :
                            originalType === 'alert' ? 'alert' :
                            originalType === 'output' ? 'alert' :
                            originalType;

            // 如果已经是 ReactFlow 格式，只更新类型
            if (node.position && node.data) {
              const currentAlgorithm = nodeType === 'algorithm'
                ? algorithms.find((algo: any) => String(algo.id) === String(node.data?.dataId ?? node.data?.algorithmId))
                : null;
              return {
                ...node,
                type: nodeType,
                data: nodeType === 'algorithm'
                  ? {
                      ...node.data,
                      algorithmType: node.data.algorithmType || currentAlgorithm?.algorithm_type || 'script',
                      isTemplate: Boolean(data.is_template),
                    }
                  : { ...node.data, isTemplate: Boolean(data.is_template) },
              };
            }

            const nodeData: any = {
              type: nodeType,
              subtype: node.subtype,
              label: node.name || '未命名节点',
              description: node.description,
              dataId: node.dataId || node.data_id,
              algorithmId: node.algorithmId || node.algorithm_id,
              icon: node.icon,
              color: node.color,
              config: node.config || node.data?.config,
              isTemplate: Boolean(data.is_template),
            };

            if (nodeType === 'algorithm') {
              const runtimeAlgorithmId = nodeData.dataId || nodeData.algorithmId;
              const currentAlgorithm = algorithms.find((algo: any) => String(algo.id) === String(runtimeAlgorithmId));
              nodeData.algorithmType = currentAlgorithm?.algorithm_type || node.data?.algorithmType || 'script';
              nodeData.defaultConfidence = getAlgorithmDefaultConfidence(currentAlgorithm) ?? node.data?.defaultConfidence;
              nodeData.confidence = node.config?.confidence ?? node.data?.confidence ?? nodeData.defaultConfidence;
            }

            if (nodeType === 'externalApi') {
              const currentExternalApiId = nodeData.dataId;
              const currentExternalApi = externalApis.find((api: any) => String(api.id) === String(currentExternalApiId));
              nodeData.externalApiName = currentExternalApi?.name || node.data?.externalApiName;
              nodeData.executionMode = node.config?.execution_mode || node.data?.executionMode || 'sync';
            }

            // 特殊处理：从 data 字段读取额外的配置
            if (node.data && typeof node.data === 'object') {
              // Alert 节点：读取 alertLevel, alertType, alertMessage, suppression
              if (nodeType === 'alert') {
                nodeData.alertLevel = node.data.alertLevel;
                nodeData.alertType = node.data.alertType;
                nodeData.alertMessage = node.data.alertMessage;
                nodeData.suppression = node.data.suppression;
                nodeData.vlValidation = node.data.vlValidation;
                console.log('🚨 [EDITOR] Alert 节点加载配置:', {
                  id: node.id,
                  alertLevel: nodeData.alertLevel,
                  alertType: nodeData.alertType,
                  alertMessage: nodeData.alertMessage,
                  suppression: nodeData.suppression,
                  vlValidation: nodeData.vlValidation,
                });
              }
              // Condition 节点：读取 targetCount 和 comparisonType
              if (nodeType === 'condition') {
                nodeData.conditionKind = node.data.conditionKind || node.data.condition_kind || 'count';
                nodeData.targetCount = node.data.targetCount || node.data.target_count || 1;
                nodeData.comparisonType = node.data.comparisonType || node.data.comparison_type || '>=';
                nodeData.sourceNodeId = node.data.sourceNodeId || node.data.source_node_id;
                nodeData.textOperator = node.data.textOperator || node.data.text_operator || 'contains';
                nodeData.patternType = node.data.patternType || node.data.pattern_type || 'keywords';
                nodeData.keywords = node.data.keywords || [];
                nodeData.keywordLogic = node.data.keywordLogic || node.data.keyword_logic || 'any';
                nodeData.regexPattern = node.data.regexPattern || node.data.regex_pattern || '';
                nodeData.caseSensitive = node.data.caseSensitive ?? node.data.case_sensitive ?? false;
                nodeData.labels = node.data.labels || [];
                nodeData.windowSize = node.data.windowSize ?? node.data.window_size ?? 10;
                nodeData.direction = node.data.direction || 'both';
                nodeData.relativeThreshold = node.data.relativeThreshold ?? node.data.relative_threshold ?? 0.5;
                nodeData.absoluteThreshold = node.data.absoluteThreshold ?? node.data.absolute_threshold ?? 3;
                nodeData.confirmationCount = node.data.confirmationCount ?? node.data.confirmation_count ?? 1;
                nodeData.expression = node.data.expression || {
                  logic: 'and',
                  children: [{ variable: '$success', operator: 'eq', value: true }],
                };
                console.log('🔀 [EDITOR] Condition 节点加载配置:', {
                  id: node.id,
                  targetCount: nodeData.targetCount,
                  comparisonType: nodeData.comparisonType,
                });
              }
              if (nodeType === 'timeSchedule') {
                nodeData.weeklySchedule = normalizeWeeklySchedule(node.data.weeklySchedule);
              }
              // Function 节点：读取 functionName, threshold, operator, dimension, input_nodes
              if (nodeType === 'function') {
                nodeData.functionName = node.data.functionName;
                nodeData.threshold = node.data.threshold;
                nodeData.operator = node.data.operator;
                nodeData.dimension = node.data.dimension;
                nodeData.input_nodes = node.data.input_nodes;
                console.log('🔢 [EDITOR] Function 节点加载配置:', {
                  id: node.id,
                  functionName: nodeData.functionName,
                  threshold: nodeData.threshold,
                  operator: nodeData.operator,
                  dimension: nodeData.dimension,
                  input_nodes: nodeData.input_nodes,
                });
              }
              // ROI 节点已经在后面处理
            }

            // 特殊处理：保留 videoSourceId 和 videoSourceName
            if (nodeType === 'videoSource') {
              const sourceId = node.dataId ?? node.data?.dataId ?? node.videoSourceId ?? node.data?.videoSourceId ?? node.data_id;
              nodeData.dataId = sourceId;
              nodeData.videoSourceId = sourceId;

              console.log('🔍 处理视频源节点:', {
                节点ID: node.id,
                原始节点完整数据: node,
                提取的sourceId: sourceId,
                videoSources列表长度: videoSources.length,
              });

              // 如果有 videoSourceName 直接使用，否则从 videoSources 中查找
              let sourceName = node.videoSourceName || node.data?.videoSourceName;
              let sourceCode = node.videoSourceCode || node.data?.videoSourceCode;

              console.log('📝 视频源信息初始值:', {
                sourceName,
                sourceCode,
                来源: sourceName ? '已存储在数据中' : '需要从列表查找'
              });

              // 如果没有名称或编码，从 videoSources 列表中查找
              if ((!sourceName || !sourceCode) && sourceId && videoSources.length > 0) {
                console.log('🔎 尝试从列表查找匹配的视频源, sourceId:', sourceId);
                const matchingSource = videoSources.find(s => s.id == sourceId);
                console.log('查找结果:', {
                  sourceId,
                  matchingSource: matchingSource ? `找到: ${matchingSource.name}` : '未找到',
                  所有视频源: videoSources.map(s => ({ id: s.id, name: s.name, code: s.source_code }))
                });
                if (matchingSource) {
                  if (!sourceName) {
                    sourceName = matchingSource.name;
                    console.log('✅ 使用匹配的名称:', sourceName);
                  }
                  if (!sourceCode) {
                    sourceCode = matchingSource.source_code;
                    console.log('✅ 使用匹配的编码:', sourceCode);
                  }
                } else {
                  console.warn('⚠️ 未找到匹配的视频源, sourceId:', sourceId);
                }
              }

              nodeData.videoSourceName = sourceName;
              nodeData.videoSourceCode = sourceCode;

              console.log('🎯 视频源节点最终数据:', {
                节点ID: node.id,
                videoSourceId: nodeData.videoSourceId,
                videoSourceName: nodeData.videoSourceName,
                videoSourceCode: nodeData.videoSourceCode,
              });
            }

            // ROI 节点：从 data.roiRegions 读取
            if (nodeType === 'roi') {
              nodeData.roiRegions = node.data?.roiRegions || [];
              console.log('🎯 ROI 节点加载 roiRegions:', {
                节点ID: node.id,
                区域数: nodeData.roiRegions.length,
              });
            }

            // Alert 节点：从 data 中读取所有 alert 相关字段
            if (nodeType === 'alert') {
              nodeData.alertLevel = node.data?.alertLevel || 'info';
              nodeData.alertMessage = node.data?.alertMessage || '检测到目标';
              nodeData.alertType = node.data?.alertType || 'detection';
              nodeData.messageFormat = node.data?.messageFormat || 'detailed';
              nodeData.triggerCondition = node.data?.triggerCondition;
              nodeData.suppression = node.data?.suppression;
              nodeData.vlValidation = node.data?.vlValidation;
              console.log('🚨 Alert 节点加载数据:', {
                节点ID: node.id,
                alertLevel: nodeData.alertLevel,
                alertType: nodeData.alertType,
                messageFormat: nodeData.messageFormat,
                vlValidation: nodeData.vlValidation,
              });
            }

            return {
              id: node.id,
              type: nodeType,
              position: {
                x: Number(node.x || node.position_x || 0),
                y: Number(node.y || node.position_y || 0)
              },
              data: nodeData,
            };
          });

          console.log('转换后的节点:', convertedNodes);

          // 转换连线数据
          // 优先使用 connections（后端标准格式），如果没有才使用 edges（兼容旧数据）
          const convertedEdges = (graphData.connections || graphData.edges || [])
            .map((conn: any, index: number) => {
              const fromNodeId = conn.from_node_id || conn.from;
              const toNodeId = conn.to_node_id || conn.to;
              const fromNode = convertedNodes.find((n: any) => n.id === fromNodeId);
              const toNode = convertedNodes.find((n: any) => n.id === toNodeId);

              // 验证节点存在
              if (!fromNode) {
                console.warn(`连线 ${index}: 找不到源节点 ${fromNodeId}`, conn);
                return null;
              }
              if (!toNode) {
                console.warn(`连线 ${index}: 找不到目标节点 ${toNodeId}`, conn);
                return null;
              }

              let sourceHandle = conn.from_port || conn.fromPort || 'output';
              const isConditionNode = fromNode?.type === 'condition' ||
                                     fromNode?.data?.type === 'condition';

              if (isConditionNode) {
                if (sourceHandle === 'output') {
                  sourceHandle = 'yes';
                }
                if (sourceHandle === 'true') sourceHandle = 'yes';
                if (sourceHandle === 'false') sourceHandle = 'no';

                if (sourceHandle !== 'yes' && sourceHandle !== 'no') {
                  sourceHandle = 'yes';
                }
              }

              const targetHandle = conn.to_port || conn.toPort || 'input';
              const persistedCondition = persistNonConditionEdgeCondition(conn.condition);
              const edgeLabel = edgeConditionLabel(persistedCondition) || conn.label || '';

              const edge = {
                id: conn.id || `${fromNodeId}-${toNodeId}`,
                source: fromNodeId,
                target: toNodeId,
                sourceHandle,
                targetHandle,
                type: 'smoothstep',
                label: edgeLabel,
                data: {
                  condition: persistedCondition,
                },
                markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20 },
              };

              console.log(`连线 ${index}:`, {
                原始: conn,
                转换后: edge,
                源节点类型: fromNode?.type,
                目标节点类型: toNode?.type
              });

              return edge;
            })
            .filter((edge: any) => edge !== null);

          console.log('转换后的连线:', convertedEdges);

          setNodes(convertedNodes);
          setEdges(convertedEdges);
          message.success(`加载成功：${convertedNodes.length} 个节点，${convertedEdges.length} 条连线`);
        } catch (error) {
          console.error('解析工作流图失败:', error);
          message.error('工作流数据格式错误，请联系管理员');
          // 设置空数据
          setNodes([]);
          setEdges([]);
        }
      } else {
        console.log('工作流没有图数据，初始化空画布');
        setNodes([]);
        setEdges([]);
      }
    } catch (error: any) {
      console.error('加载工作流失败:', error);
      message.error(error.message || '加载工作流失败');
      setNodes([]);
      setEdges([]);
    } finally {
      setLoading(false);
    }
  };

  const loadVideoSources = async () => {
    try {
      const data = await getVideoSources();
      setVideoSources(data || []);
    } catch (error) {
      console.error('加载视频源失败:', error);
    }
  };

  const loadAlgorithms = async () => {
    try {
      const data = await getAlgorithms();
      setAlgorithms(data || []);
    } catch (error) {
      console.error('加载算法失败:', error);
      setAlgorithms([]);
    }
  };

  const loadExternalApis = async () => {
    try {
      const data = await getExternalApis();
      setExternalApis(data || []);
    } catch (error) {
      console.error('加载外部 API 失败:', error);
      setExternalApis([]);
    }
  };

  const loadVlConfig = async () => {
    try {
      const data = await getVlConfig();
      setVlConfig(data?.config || null);
    } catch (error) {
      console.error('加载 VL 配置失败:', error);
      setVlConfig(null);
    }
  };

  const onConnect = (params: Connection) => {
    const sourceNode = nodes.find((item) => item.id === params.source);
    const targetNode = nodes.find((item) => item.id === params.target);
    const sourceType = sourceNode?.data?.type || sourceNode?.type;
    const targetType = targetNode?.data?.type || targetNode?.type;

    if (sourceType === 'webhook') {
      message.warning('Webhook 是终端节点，不能连接下游节点');
      return;
    }
    if (targetType === 'webhook' && sourceType !== 'alert') {
      message.warning('Webhook 只能直接连接告警输出节点');
      return;
    }
    if (sourceType === 'alert' && targetType !== 'webhook') {
      message.warning('告警输出节点的下游只能是 Webhook 推送节点');
      return;
    }
    if (targetType === 'webhook' && edges.some((edge) => edge.target === params.target)) {
      message.warning('Webhook 只能连接一个告警输出节点');
      return;
    }
    if (targetType === 'detectionFilter' || targetType === 'detection_filter') {
      const allowedSourceTypes = new Set([
        'algorithm', 'externalApi', 'external_api', 'function',
        'detectionFilter', 'detection_filter',
      ]);
      if (!allowedSourceTypes.has(sourceType)) {
        message.warning('目标尺寸筛选只能连接检测结果节点');
        return;
      }
      if (edges.some((edge) => edge.target === params.target)) {
        message.warning('目标尺寸筛选只能连接一个上游结果节点');
        return;
      }
    }
    const targetConditionKind = targetNode?.data?.conditionKind || targetNode?.data?.condition_kind;
    if (
      targetType === 'condition'
      && (targetConditionKind === 'count_change' || targetConditionKind === 'http_value')
      && edges.some((edge) => edge.target === params.target)
    ) {
      message.warning('当前条件类型只能连接一个上游结果节点');
      return;
    }
    if (targetType === 'condition' && targetConditionKind === 'http_value' && sourceType !== 'httpRequest') {
      message.warning('API 值条件只能连接 HTTP 请求节点');
      return;
    }

    setEdges((currentEdges) => addEdge({
      ...params,
      markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20 },
    }, currentEdges));
  };

  const onNodeClick = (_: any, node: Node) => {
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
    setRightPanel('properties');
  };

  const onEdgeClick = (_: any, edge: Edge) => {
    setSelectedEdgeId(edge.id);
    setSelectedNodeId(null);
    setRightPanel('properties');
  };

  const onPaneClick = () => {
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
  };

  const onSelectionChange = ({ nodes: selectedNodes, edges: selectedEdges }: any) => {
    if (selectedNodes && selectedNodes.length > 0) {
      setSelectedNodeId(selectedNodes[0].id);
      setSelectedEdgeId(null);
      setRightPanel('properties');
    } else if (selectedEdges && selectedEdges.length > 0) {
      setSelectedEdgeId(selectedEdges[0].id);
      setSelectedNodeId(null);
      setRightPanel('properties');
    } else {
      setSelectedNodeId(null);
      setSelectedEdgeId(null);
    }
  };

  const getSourceNodes = (targetNodes: any[] = nodes) => {
    return targetNodes.filter((node) => {
      const nodeType = node.data?.type || node.type;
      return nodeType === 'videoSource' || nodeType === 'source';
    });
  };

  const handleSave = async (options?: { silent?: boolean }) => {
    const silent = Boolean(options?.silent);
    if (!silent) {
      setSaving(true);
    }
    try {
      console.log('💾 ============ [EDITOR] handleSave 开始 ============');
      console.log('📊 [EDITOR] 当前nodes数量:', nodes.length);

      const sourceNodes = getSourceNodes(nodes);
      if (sourceNodes.length !== 1) {
        const errorMsg = sourceNodes.length === 0
          ? '工作流必须包含一个视频源节点'
          : '工作流只允许包含一个视频源节点';
        console.warn('⚠️ [EDITOR] 保存被阻止，source 节点数量非法:', sourceNodes.length);
        if (!silent) {
          message.error(errorMsg);
        }
        return false;
      }
      const sourceNode = sourceNodes[0];
      const sourceId = sourceNode.data?.dataId ?? sourceNode.data?.videoSourceId;
      if (workflow?.is_template && sourceId != null && sourceId !== '') {
        if (!silent) message.error('编排模板的视频源节点必须保持未绑定状态');
        return false;
      }
      if (!workflow?.is_template && (sourceId == null || sourceId === '')) {
        if (!silent) message.error('请选择视频源');
        return false;
      }

      // 打印所有当前节点的videoSourceId
      console.log('📊 [EDITOR] 保存前所有节点的videoSourceId:',
        nodes.map(n => ({ id: n.id, type: n.data?.type, videoSourceId: n.data?.videoSourceId }))
      );

      const saveNodes = nodes.map(node => {
        // 重要：后端期望的类型是 'source', 'algorithm' 等，不是 'videoSource'
        // 所以需要映射内部类型到后端类型
        const nodeType = node.data?.type || node.type;
        const backendType = nodeType === 'videoSource'
          ? 'source'
          : nodeType === 'externalApi'
            ? 'external_api'
          : nodeType === 'httpRequest'
            ? 'http_request'
            : nodeType === 'detectionFilter'
              ? 'detection_filter'
            : nodeType === 'timeSchedule'
              ? 'time_schedule'
            : nodeType;

        const saveData: any = {
          id: node.id,
          type: backendType,  // 使用映射后的类型
          subtype: node.data?.subtype || null,
          name: node.data?.label || node.data?.name,
          x: node.position?.x || 0,
          y: node.position?.y || 0,
          description: node.data?.description || null,
          config: node.data?.config || null,
        };

        // 根据节点类型保存不同的字段
        if (nodeType === 'videoSource' || nodeType === 'source') {
          // 后端使用 dataId 字段存储视频源ID
          const sourceId = node.data?.dataId ?? node.data?.videoSourceId ?? null;
          const selectedSource = sourceId != null
            ? videoSources.find((source) => String(source.id) === String(sourceId))
            : null;
          saveData.dataId = sourceId;

          // 额外保存这些字段用于前端显示（后端不使用，但保存后前端加载时需要）
          saveData.videoSourceId = sourceId;
          saveData.videoSourceName = selectedSource?.name || node.data?.videoSourceName;
          saveData.videoSourceCode = selectedSource?.source_code || node.data?.videoSourceCode;

          console.log('🎥 [EDITOR] 视频源节点保存数据:', {
            id: node.id,
            内部类型: nodeType,
            保存类型: backendType,
            从node_data读取的dataId: node.data?.dataId,
            从node_data读取的videoSourceId: node.data?.videoSourceId,
            dataId: saveData.dataId,
            videoSourceId: saveData.videoSourceId,
            videoSourceName: saveData.videoSourceName,
            videoSourceCode: saveData.videoSourceCode,
          });
        } else if (nodeType === 'roi') {
          // ROI 节点：保存 roiRegions 到 data 字段
          const roiRegions = node.data?.roiRegions || [];
          saveData.data = {
            roiRegions: roiRegions
          };
          console.log('🎯 [EDITOR] ROI 节点保存数据:', {
            id: node.id,
            区域数: roiRegions.length,
            区域列表: roiRegions.map((r: any) => r.name),
          });
          saveData.dataId = node.data?.dataId;
        } else if (nodeType === 'externalApi') {
          const externalApiId = node.data?.dataId || null;
          const selectedExternalApi = externalApiId != null
            ? externalApis.find((item) => String(item.id) === String(externalApiId))
            : null;
          saveData.dataId = externalApiId;
          saveData.data = {
            externalApiName: selectedExternalApi?.name || node.data?.externalApiName,
          };
        } else if (nodeType === 'httpRequest') {
          saveData.config = node.data?.config || {};
        } else if (nodeType === 'function') {
          // 函数节点：所有配置都在 config 中，input_nodes 也在 data 中
          saveData.data = {
            input_nodes: node.data?.input_nodes,
          };
          console.log('🔢 [EDITOR] Function 节点保存数据:', {
            id: node.id,
            config: saveData.config,
            input_nodes: saveData.data.input_nodes,
          });
        } else if (nodeType === 'alert') {
          // Alert 节点：保存 alertLevel, alertType, alertMessage, messageFormat, triggerCondition, suppression 到 data 字段
          saveData.data = {
            alertLevel: node.data?.alertLevel,
            alertType: node.data?.alertType,
            alertMessage: node.data?.alertMessage,
            messageFormat: node.data?.messageFormat || 'detailed',  // 添加消息格式字段
            triggerCondition: node.data?.triggerCondition,
            suppression: node.data?.suppression,
            vlValidation: node.data?.vlValidation,
          };
          console.log('🚨 [EDITOR] Alert 节点保存数据:', {
            id: node.id,
            alertLevel: saveData.data.alertLevel,
            alertType: saveData.data.alertType,
            alertMessage: saveData.data.alertMessage,
            messageFormat: saveData.data.messageFormat,  // 添加日志
            triggerCondition: saveData.data.triggerCondition,
            suppression: saveData.data.suppression,
            vlValidation: saveData.data.vlValidation,
          });
        } else if (nodeType === 'condition') {
          // Condition 节点：保存 targetCount 和 comparisonType 到 data 字段
          saveData.data = {
            conditionKind: node.data?.conditionKind || node.data?.condition_kind || 'count',
            targetCount: node.data?.targetCount || node.data?.target_count || 1,
            comparisonType: node.data?.comparisonType || node.data?.comparison_type || '>=',
            sourceNodeId: node.data?.sourceNodeId || node.data?.source_node_id,
            textOperator: node.data?.textOperator || node.data?.text_operator || 'contains',
            patternType: node.data?.patternType || node.data?.pattern_type || 'keywords',
            keywords: node.data?.keywords || [],
            keywordLogic: node.data?.keywordLogic || node.data?.keyword_logic || 'any',
            regexPattern: node.data?.regexPattern || node.data?.regex_pattern || '',
            caseSensitive: node.data?.caseSensitive ?? node.data?.case_sensitive ?? false,
            labels: node.data?.labels || [],
            windowSize: node.data?.windowSize ?? node.data?.window_size ?? 10,
            direction: node.data?.direction || 'both',
            relativeThreshold: node.data?.relativeThreshold ?? node.data?.relative_threshold ?? 0.5,
            absoluteThreshold: node.data?.absoluteThreshold ?? node.data?.absolute_threshold ?? 3,
            confirmationCount: node.data?.confirmationCount ?? node.data?.confirmation_count ?? 1,
            expression: node.data?.expression || {
              logic: 'and',
              children: [{ variable: '$success', operator: 'eq', value: true }],
            },
          };
          console.log('🔀 [EDITOR] Condition 节点保存数据:', {
            id: node.id,
            targetCount: saveData.data.targetCount,
            comparisonType: saveData.data.comparisonType,
          });
        } else if (nodeType === 'timeSchedule' || nodeType === 'time_schedule') {
          saveData.data = {
            weeklySchedule: normalizeWeeklySchedule(node.data?.weeklySchedule),
          };
        } else {
          saveData.dataId = node.data?.dataId;
          saveData.algorithmId = node.data?.algorithmId || null;
        }

        return saveData;
      });

      const connections = edges.map(edge => {
        const fromNode = nodes.find(n => n.id === edge.source);
        let fromPort = edge.sourceHandle || 'output';
        let condition = null;

        if (isConditionNodeType(fromNode)) {
          if (fromPort === 'yes') {
            fromPort = 'true';
            condition = 'true';
          }
          if (fromPort === 'no') {
            fromPort = 'false';
            condition = 'false';
          }
        } else {
          condition = persistNonConditionEdgeCondition(edge.data?.condition);
        }

        return {
          id: edge.id,
          from: edge.source,
          to: edge.target,
          from_node_id: edge.source,
          to_node_id: edge.target,
          from_port: fromPort,
          to_port: edge.targetHandle || 'input',
          condition,
          label: edge.label || '',
        };
      });

      // 只保存 connections（后端格式），不保存 edges
      // 因为加载时我们会根据 connections 重建 edges
      const graphData = {
        nodes: saveNodes,
        connections,
      };

      console.log('💾 [EDITOR] 准备提交给后端的数据:', JSON.stringify({
        nodes: saveNodes.length,
        connections: connections.length,
        所有节点: saveNodes.map(n => ({
          id: n.id,
          type: n.type,
          name: n.name,
          dataId: n.dataId,
          videoSourceId: n.videoSourceId,
          videoSourceName: n.videoSourceName,
        }))
      }, null, 2));

      await updateWorkflow(Number(id), { workflow_data: graphData });
      if (!silent) {
        message.success('保存成功');
      }
      console.log('✅ [EDITOR] 保存成功');
      return true;
    } catch (error: any) {
      console.error('❌ [EDITOR] 保存失败:', error);
      const saveError = getApiErrorMessage(error) || '保存失败';
      if (!silent) {
        message.error(saveError);
        return false;
      }
      return saveError;
    } finally {
      if (!silent) {
        setSaving(false);
      }
    }
  };

  const handleAddNode = (nodeData: any) => {
    console.log('🚀 [EDITOR] handleAddNode 收到的数据:', nodeData);

    if (nodeData.type === 'videoSource' && getSourceNodes().length > 0) {
      message.warning('一个编排只允许一个视频源节点');
      return;
    }

    const newNode: Node = {
      id: `${nodeData.type}-${Date.now()}`,
      type: nodeData.nodeType,
      position: { x: Math.random() * 400 + 100, y: Math.random() * 300 + 100 },
      data: {
        type: nodeData.type,
        label: nodeData.label,
        description: nodeData.description,
        dataId: nodeData.dataId || null,
        algorithmId: nodeData.algorithmId || null,
        defaultConfidence: nodeData.defaultConfidence,
        confidence: nodeData.defaultConfidence,
        icon: nodeData.icon,
        color: nodeData.color,
        config: nodeData.config || {},  // 使用传入的 config，而不是 null
        alertLevel: nodeData.alertLevel,
        alertMessage: nodeData.alertMessage,
        alertType: nodeData.alertType,
        messageFormat: nodeData.messageFormat,
        triggerCondition: nodeData.triggerCondition,
        suppression: nodeData.suppression,
        vlValidation: nodeData.vlValidation,
        externalApiName: nodeData.externalApiName,
        executionMode: nodeData.config?.execution_mode || 'sync',
        conditionKind: nodeData.conditionKind,
        labels: nodeData.labels,
        windowSize: nodeData.windowSize,
        direction: nodeData.direction,
        relativeThreshold: nodeData.relativeThreshold,
        absoluteThreshold: nodeData.absoluteThreshold,
        confirmationCount: nodeData.confirmationCount,
        expression: nodeData.expression,
        weeklySchedule: nodeData.type === 'timeSchedule'
          ? (nodeData.weeklySchedule || createDefaultWeeklySchedule())
          : undefined,
        // ROI 节点初始化空的 roiRegions 数组
        ...(nodeData.type === 'roi' ? { roiRegions: [] } : {}),
      },
    };

    console.log('✅ [EDITOR] 创建的新节点:', newNode);
    setNodes((nds) => [...nds, newNode]);
  };

  const handleUpdateNode = (nodeId: string, updatedData: any) => {
    setNodes((currentNodes) => currentNodes.map((node) => (
      node.id === nodeId
        ? { ...node, data: { ...node.data, ...updatedData } }
        : node
    )));
  };

  const handleDeleteNode = (nodeId: string) => {
    setNodes((nds) => nds.filter((n) => n.id !== nodeId));
    setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
    setSelectedNodeId((currentId) => currentId === nodeId ? null : currentId);
    setSelectedEdgeId(null);
    message.success('节点删除成功');
  };

  const handleUpdateEdge = (edgeId: string, data: { condition: 'detected' | 'not_detected' | null }) => {
    const condition = persistNonConditionEdgeCondition(data.condition);
    setEdges((currentEdges) => currentEdges.map((edge) => (
      edge.id === edgeId
        ? {
            ...edge,
            label: edgeConditionLabel(condition),
            data: { ...edge.data, condition },
          }
        : edge
    )));
  };

  const handleDeleteEdge = (edgeId: string) => {
    setEdges((currentEdges) => currentEdges.filter((edge) => edge.id !== edgeId));
    setSelectedEdgeId((currentId) => currentId === edgeId ? null : currentId);
    message.success('连线删除成功');
  };

  const deleteSelected = () => {
    const selectedNodeIds = new Set(nodes.filter((node) => node.selected).map((node) => node.id));
    const selectedEdgeIds = new Set(edges.filter((edge) => edge.selected).map((edge) => edge.id));

    if (selectedNodeIds.size > 0) {
      setNodes((nds) => nds.filter((node) => !selectedNodeIds.has(node.id)));
      setEdges((eds) =>
        eds.filter((edge) => !selectedNodeIds.has(edge.source) && !selectedNodeIds.has(edge.target))
      );
      setSelectedNodeId(null);
      setSelectedEdgeId(null);
      message.success(`已删除 ${selectedNodeIds.size} 个节点`);
      return;
    }

    if (selectedEdgeIds.size > 0) {
      setEdges((eds) => eds.filter((edge) => !selectedEdgeIds.has(edge.id)));
      setSelectedEdgeId(null);
      message.success(`已删除 ${selectedEdgeIds.size} 条连线`);
      return;
    }

    // 属性面板的目标作为键盘选中状态丢失时的保底，且仍使用显式 ID。
    if (selectedNodeId) {
      handleDeleteNode(selectedNodeId);
      return;
    }
    if (selectedEdgeId) {
      handleDeleteEdge(selectedEdgeId);
    }
  };

  const handleBack = () => {
    navigate('/workflows');
  };

  if (loading) {
    return (
      <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" tip="加载中..." />
      </div>
    );
  }

  return (
    <div className="workflow-editor-page" style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* 头部工具栏 */}
      <div className="editor-header">
        <div className="header-left">
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={handleBack}
            style={{ marginRight: 16 }}
          >
            返回
          </Button>
          <div className="header-content">
            <h3 className="header-title">{workflow?.name || '算法编排编辑器'}</h3>
            <p className="header-subtitle">
              {workflow?.is_template ? '配置可复用结构，视频源将在复制时绑定' : '拖拽组件到画布，连线配置算法编排'}
            </p>
          </div>
          {workflow?.is_template ? (
            <Tag color="purple" icon={<FileTextOutlined />} className="editor-template-tag">
              编排模板 · 不调度
            </Tag>
          ) : null}
        </div>
        <div className="header-right">
          <Space size="small">
            <Button
              icon={<DeleteOutlined />}
              onClick={deleteSelected}
              disabled={!selectedNodeId && !selectedEdgeId && !nodes.some((node) => node.selected) && !edges.some((edge) => edge.selected)}
              danger
            >
              删除
            </Button>
            <Button
              icon={<ExperimentOutlined />}
              onClick={() => setRightPanel('test')}
              className={rightPanel === 'test' ? 'active' : ''}
            >
              测试
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={saving}
              disabled={saving}
              onClick={() => void handleSave()}
            >
              保存
            </Button>
          </Space>
        </div>
      </div>

      {/* 编辑器主体 */}
      <div className="editor-body" style={{ flex: 1, overflow: 'hidden' }}>
        {/* 左侧组件面板 */}
        <ComponentSidebar
          onAddNode={handleAddNode}
          videoSources={videoSources}
          hasSourceNode={getSourceNodes().length > 0}
        />

        {/* 中间画布 */}
        <div className="editor-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onEdgeClick={onEdgeClick}
            onPaneClick={onPaneClick}
            onSelectionChange={onSelectionChange}
            onNodesDelete={(deletedNodes) => {
              const deletedIds = new Set(deletedNodes.map((node) => node.id));
              setSelectedNodeId((currentId) => currentId && deletedIds.has(currentId) ? null : currentId);
              setSelectedEdgeId(null);
            }}
            onEdgesDelete={(deletedEdges) => {
              const deletedIds = new Set(deletedEdges.map((edge) => edge.id));
              setSelectedEdgeId((currentId) => currentId && deletedIds.has(currentId) ? null : currentId);
            }}
            nodeTypes={nodeTypes}
            fitView
            deleteKeyCode="Delete"
          >
            <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
            <Controls />
            <MiniMap
              nodeColor={(node) => {
                switch (node.type) {
                  case 'videoSource': return '#1890ff';
                  case 'algorithm': return '#52c41a';
                  case 'externalApi': return '#1677ff';
                  case 'httpRequest': return '#08979c';
                  case 'function': return '#722ed1';
                  case 'detectionFilter': return '#531dab';
                  case 'condition': return '#faad14';
                  case 'timeSchedule': return '#2f54eb';
                  case 'roi': return '#fa8c16';
                  case 'alert': return '#f5222d';
                  case 'webhook': return '#13c2c2';
                  default: return '#d9d9d9';
                }
              }}
            />
          </ReactFlow>
        </div>

        {/* 右侧属性面板 */}
        <div className="editor-properties">
          {rightPanel === 'properties' ? (
            selectedEdge ? (
              <EdgePropertyPanel
                edge={selectedEdge}
                nodes={nodes}
                onUpdate={handleUpdateEdge}
                onDelete={handleDeleteEdge}
              />
            ) : selectedNode ? (
              <PropertyPanel
                node={selectedNode}
                videoSources={videoSources}
                algorithms={algorithms}
                externalApis={externalApis}
                vlConfig={vlConfig}
                edges={edges}
                nodes={nodes}
                isTemplate={Boolean(workflow?.is_template)}
                onUpdate={handleUpdateNode}
                onDelete={handleDeleteNode}
              />
            ) : (
              <div className="property-panel-empty">
                <AppEmptyState
                  compact
                  title="点击节点或连线查看属性"
                  description="点击画布中的节点或连线以编辑其属性"
                />
              </div>
            )
          ) : (
            <TestPanel
              workflow={workflow}
              nodes={nodes}
              edges={edges}
              videoSources={videoSources}
              onBeforeTest={() => handleSave({ silent: true })}
            />
          )}
        </div>
      </div>
    </div>
  );
}
