import React, { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'umi';
import { Button, Space, message, Spin, Empty } from 'antd';
import {
  CloseOutlined,
  SaveOutlined,
  ExperimentOutlined,
  DeleteOutlined,
  ArrowLeftOutlined,
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
import PropertyPanel from '../components/PropertyPanel';
import TestPanel from '../components/TestPanel';
import { getWorkflow, updateWorkflow, getVideoSources } from '@/services/api';
import '../components/WorkflowEditor.css';

export default function WorkflowEditorPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [rightPanel, setRightPanel] = useState<'properties' | 'test'>('properties');
  const [workflow, setWorkflow] = useState<any>(null);
  const [videoSources, setVideoSources] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [videoSourcesLoaded, setVideoSourcesLoaded] = useState(false);

  // 使用 ref 保存 selectedNode 的最新值，防止闭包陷阱
  const selectedNodeRef = useRef<Node | null>(null);
  // 使用 ref 防止 onSelectionChange 覆盖正在更新的节点
  const isUpdatingNodeRef = useRef(false);

  // 当 selectedNode 变化时，同步更新 ref
  useEffect(() => {
    console.log('🔄 [EDITOR] useEffect: selectedNode 变化');
    console.log('📝 [EDITOR] 新的 selectedNode:', selectedNode);
    selectedNodeRef.current = selectedNode;
    console.log('📌 [EDITOR] selectedNodeRef.current 已更新:', {
      是否存在: !!selectedNodeRef.current,
      id: selectedNodeRef.current?.id,
      videoSourceId: selectedNodeRef.current?.data?.videoSourceId
    });
  }, [selectedNode]);

  // 先加载视频源列表，再加载工作流数据
  useEffect(() => {
    const loadAllData = async () => {
      setLoading(true);
      await loadVideoSources();
      setVideoSourcesLoaded(true);
    };
    loadAllData();
  }, [id]);

  // 视频源加载完成后，加载工作流数据
  useEffect(() => {
    if (videoSourcesLoaded) {
      loadWorkflowData();
    }
  }, [videoSourcesLoaded, id]);

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
                            originalType === 'function' ? 'function' :
                            originalType === 'condition' ? 'condition' :
                            originalType === 'roi' ? 'roi' :
                            originalType === 'alert' ? 'alert' :
                            originalType === 'output' ? 'alert' :
                            originalType;

            // 如果已经是 ReactFlow 格式，只更新类型
            if (node.position && node.data) {
              return {
                ...node,
                type: nodeType,
              };
            }

            const nodeData: any = {
              type: node.type,
              subtype: node.subtype,
              label: node.name || '未命名节点',
              description: node.description,
              dataId: node.dataId || node.data_id,
              algorithmId: node.algorithmId || node.algorithm_id,
              icon: node.icon,
              color: node.color,
              config: node.config || node.data?.config,
            };

            // 特殊处理：从 data 字段读取额外的配置
            if (node.data && typeof node.data === 'object') {
              // Alert 节点：读取 alertLevel, alertType, alertMessage, suppression
              if (nodeType === 'alert') {
                nodeData.alertLevel = node.data.alertLevel;
                nodeData.alertType = node.data.alertType;
                nodeData.alertMessage = node.data.alertMessage;
                nodeData.suppression = node.data.suppression;
                console.log('🚨 [EDITOR] Alert 节点加载配置:', {
                  id: node.id,
                  alertLevel: nodeData.alertLevel,
                  alertType: nodeData.alertType,
                  alertMessage: nodeData.alertMessage,
                  suppression: nodeData.suppression,
                });
              }
              // Condition 节点：读取 targetCount 和 comparisonType
              if (nodeType === 'condition') {
                nodeData.targetCount = node.data.targetCount || node.data.target_count || 1;
                nodeData.comparisonType = node.data.comparisonType || node.data.comparison_type || '>=';
                console.log('🔀 [EDITOR] Condition 节点加载配置:', {
                  id: node.id,
                  targetCount: nodeData.targetCount,
                  comparisonType: nodeData.comparisonType,
                });
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
              const sourceId = node.videoSourceId || node.data?.videoSourceId || node.dataId || node.data_id;
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
              console.log('🚨 Alert 节点加载数据:', {
                节点ID: node.id,
                alertLevel: nodeData.alertLevel,
                alertType: nodeData.alertType,
                messageFormat: nodeData.messageFormat,
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

              const edge = {
                id: conn.id || `${fromNodeId}-${toNodeId}`,
                source: fromNodeId,
                target: toNodeId,
                sourceHandle,
                targetHandle,
                type: 'smoothstep',
                label: conn.label || '',
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

  const onConnect = (params: Connection) => setEdges((eds) => addEdge({
    ...params,
    markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20 },
  }, eds));

  const onNodeClick = (_: any, node: Node) => {
    setSelectedNode(node);
    setRightPanel('properties');
  };

  const onPaneClick = () => {
    setSelectedNode(null);
  };

  const onSelectionChange = ({ nodes: selectedNodes, edges: selectedEdges }: any) => {
    // 如果正在更新节点，不要覆盖 selectedNode
    if (isUpdatingNodeRef.current) {
      console.log('⏸️ [EDITOR] onSelectionChange 跳过，正在更新节点');
      return;
    }

    if (selectedNodes && selectedNodes.length > 0) {
      setSelectedNode(selectedNodes[0]);
      setRightPanel('properties');
    } else {
      if (!selectedEdges || selectedEdges.length === 0) {
        setSelectedNode(null);
      }
    }
  };

  const handleSave = async (options?: { silent?: boolean }) => {
    const silent = Boolean(options?.silent);
    try {
      console.log('💾 ============ [EDITOR] handleSave 开始 ============');
      console.log('📊 [EDITOR] 当前nodes数量:', nodes.length);

      // 打印所有当前节点的videoSourceId
      console.log('📊 [EDITOR] 保存前所有节点的videoSourceId:',
        nodes.map(n => ({ id: n.id, type: n.data?.type, videoSourceId: n.data?.videoSourceId }))
      );

      const saveNodes = nodes.map(node => {
        // 重要：后端期望的类型是 'source', 'algorithm' 等，不是 'videoSource'
        // 所以需要映射内部类型到后端类型
        const nodeType = node.data?.type || node.type;
        const backendType = nodeType === 'videoSource' ? 'source' : nodeType;

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
          const videoSourceId = node.data?.videoSourceId || node.data?.dataId;
          saveData.dataId = videoSourceId;

          // 额外保存这些字段用于前端显示（后端不使用，但保存后前端加载时需要）
          saveData.videoSourceId = videoSourceId;
          saveData.videoSourceName = node.data?.videoSourceName;
          saveData.videoSourceCode = node.data?.videoSourceCode;

          console.log('🎥 [EDITOR] 视频源节点保存数据:', {
            id: node.id,
            内部类型: nodeType,
            保存类型: backendType,
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
          };
          console.log('🚨 [EDITOR] Alert 节点保存数据:', {
            id: node.id,
            alertLevel: saveData.data.alertLevel,
            alertType: saveData.data.alertType,
            alertMessage: saveData.data.alertMessage,
            messageFormat: saveData.data.messageFormat,  // 添加日志
            triggerCondition: saveData.data.triggerCondition,
            suppression: saveData.data.suppression,
          });
        } else if (nodeType === 'condition') {
          // Condition 节点：保存 targetCount 和 comparisonType 到 data 字段
          saveData.data = {
            targetCount: node.data?.targetCount || node.data?.target_count || 1,
            comparisonType: node.data?.comparisonType || node.data?.comparison_type || '>=',
          };
          console.log('🔀 [EDITOR] Condition 节点保存数据:', {
            id: node.id,
            targetCount: saveData.data.targetCount,
            comparisonType: saveData.data.comparisonType,
          });
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

        if (fromNode?.data?.type === 'condition' || fromNode?.type === 'condition') {
          if (fromPort === 'yes') {
            fromPort = 'true';
            condition = 'true';
          }
          if (fromPort === 'no') {
            fromPort = 'false';
            condition = 'false';
          }
        }

        return {
          id: edge.id,
          from: edge.source,
          to: edge.target,
          from_node_id: edge.source,
          to_node_id: edge.target,
          from_port: fromPort,
          to_port: edge.targetHandle || 'input',
          condition: condition,
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
    } catch (error) {
      console.error('❌ [EDITOR] 保存失败:', error);
      if (!silent) {
        message.error('保存失败');
      }
      return false;
    }
  };

  const handleAddNode = (nodeData: any) => {
    console.log('🚀 [EDITOR] handleAddNode 收到的数据:', nodeData);

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
        icon: nodeData.icon,
        color: nodeData.color,
        config: nodeData.config || {},  // 使用传入的 config，而不是 null
        // ROI 节点初始化空的 roiRegions 数组
        ...(nodeData.type === 'roi' ? { roiRegions: [] } : {}),
      },
    };

    console.log('✅ [EDITOR] 创建的新节点:', newNode);
    setNodes((nds) => [...nds, newNode]);
  };

  const handleUpdateNode = (updatedData: any) => {
    console.log('🚀🚀🚀 [EDITOR] handleUpdateNode 函数入口 🚀🚀🚀');
    console.log('🔍 [EDITOR] selectedNode (state) 是否存在:', !!selectedNode);
    console.log('🔍 [EDITOR] selectedNodeRef.current 是否存在:', !!selectedNodeRef.current);

    // 使用 ref 而不是 state，防止闭包陷阱
    const currentNode = selectedNodeRef.current;
    console.log('🔍 [EDITOR] currentNode 值:', currentNode);

    if (!currentNode) {
      console.warn('⚠️ [EDITOR] currentNode 为空，无法更新');
      console.trace('调用栈：');
      return;
    }

    // 设置标志，防止 onSelectionChange 覆盖
    isUpdatingNodeRef.current = true;
    console.log('🔒 [EDITOR] 设置 isUpdatingNodeRef.current = true');

    console.log('🔄 [EDITOR] handleUpdateNode 开始执行');
    console.log('📥 [EDITOR] 更新数据:', updatedData);
    console.log('📍 [EDITOR] 当前节点ID:', currentNode.id);
    console.log('📦 [EDITOR] 当前节点data:', currentNode.data);

    // 打印所有当前节点的videoSourceId
    console.log('📊 [EDITOR] 所有当前节点的videoSourceId:',
      nodes.map(n => ({ id: n.id, type: n.data?.type, videoSourceId: n.data?.videoSourceId }))
    );

    const updatedNodes = nodes.map((node) => {
      if (node.id === currentNode.id) {
        const newNode = {
          ...node,
          data: {
            ...node.data,
            ...updatedData,
          },
        };
        console.log('✅ [EDITOR] 更新后的节点:', {
          id: newNode.id,
          videoSourceId: newNode.data.videoSourceId,
          videoSourceName: newNode.data.videoSourceName,
          videoSourceCode: newNode.data.videoSourceCode,
        });
        return newNode;
      }
      return node;
    });

    // 打印更新后所有节点的videoSourceId
    console.log('📊 [EDITOR] 更新后所有节点的videoSourceId:',
      updatedNodes.map(n => ({ id: n.id, type: n.data?.type, videoSourceId: n.data?.videoSourceId }))
    );

    setNodes(updatedNodes);
    const newSelectedNode = { ...currentNode, data: { ...currentNode.data, ...updatedData } };
    console.log('🎯 [EDITOR] 设置新的 selectedNode:', {
      id: newSelectedNode.id,
      videoSourceId: newSelectedNode.data.videoSourceId,
      videoSourceName: newSelectedNode.data.videoSourceName,
    });
    setSelectedNode(newSelectedNode);

    // 使用 setTimeout 延迟重置标志，确保所有状态更新完成
    setTimeout(() => {
      isUpdatingNodeRef.current = false;
      console.log('🔓 [EDITOR] 重置 isUpdatingNodeRef.current = false');
    }, 100);

    message.success('节点更新成功');
  };

  const handleDeleteNode = () => {
    if (!selectedNode) return;

    setNodes((nds) => nds.filter((n) => n.id !== selectedNode.id));
    setEdges((eds) => eds.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id));
    setSelectedNode(null);
    message.success('节点删除成功');
  };

  const deleteSelected = () => {
    const hasSelectedNodes = nodes.some((n) => n.selected);
    const hasSelectedEdges = edges.some((e) => e.selected);

    // 优先处理选中的节点（如果右侧面板选中的节点）
    if (selectedNode) {
      handleDeleteNode();
      return;
    }

    // 如果有选中的节点（多选），删除节点和相关连线
    if (hasSelectedNodes) {
      const selectedIds = new Set(nodes.filter((n) => n.selected).map((n) => n.id));
      setNodes((nds) => nds.filter((n) => !n.selected));
      setEdges((eds) =>
        eds.filter((e) => !selectedIds.has(e.source) && !selectedIds.has(e.target))
      );
      message.success(`已删除 ${selectedIds.size} 个节点`);
      return;
    }

    // 如果只有选中的连线，只删除连线（不删除节点）
    if (hasSelectedEdges) {
      const selectedCount = edges.filter((e) => e.selected).length;
      setEdges((eds) => eds.filter((e) => !e.selected));
      message.success(`已删除 ${selectedCount} 条连线`);
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
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
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
            <p className="header-subtitle">拖拽组件到画布，连线配置算法编排</p>
          </div>
        </div>
        <div className="header-right">
          <Space size="small">
            <Button
              icon={<DeleteOutlined />}
              onClick={deleteSelected}
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
            <Button type="primary" icon={<SaveOutlined />} onClick={() => void handleSave()}>
              保存
            </Button>
          </Space>
        </div>
      </div>

      {/* 编辑器主体 */}
      <div className="editor-body" style={{ flex: 1, overflow: 'hidden' }}>
        {/* 左侧组件面板 */}
        <ComponentSidebar onAddNode={handleAddNode} videoSources={videoSources} />

        {/* 中间画布 */}
        <div className="editor-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            onSelectionChange={onSelectionChange}
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
                  case 'function': return '#722ed1';
                  case 'condition': return '#faad14';
                  case 'roi': return '#fa8c16';
                  case 'alert': return '#f5222d';
                  default: return '#d9d9d9';
                }
              }}
            />
          </ReactFlow>
        </div>

        {/* 右侧属性面板 */}
        <div className="editor-properties">
          {rightPanel === 'properties' ? (
            selectedNode ? (
              <PropertyPanel
                node={selectedNode}
                videoSources={videoSources}
                edges={edges}
                nodes={nodes}
                onUpdate={handleUpdateNode}
                onDelete={handleDeleteNode}
              />
            ) : (
              <div className="property-panel-empty">
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    <Space direction="vertical" size="small">
                      <span style={{ fontSize: 14, color: '#262626', fontWeight: 500 }}>
                        点击节点查看属性
                      </span>
                      <span style={{ fontSize: 12, color: '#8c8c8c' }}>
                        点击画布中的节点以编辑其属性
                      </span>
                    </Space>
                  }
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
