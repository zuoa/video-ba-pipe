import React, { useMemo } from 'react';
import { Modal, Badge, Tag, Space, Descriptions, Image } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  CloseOutlined,
} from '@ant-design/icons';
import ReactFlow, {
  Node,
  Edge,
  Background,
  Controls,
  MiniMap,
  NodeTypes,
  EdgeTypes,
  BackgroundVariant,
  Position,
  MarkerType,
  Handle,
  getBezierPath,
  EdgeLabelRenderer,
  EdgeText,
} from 'reactflow';
import 'reactflow/dist/style.css';
import './TestResultModal.css';

export interface TestResultModalProps {
  visible: boolean;
  onClose: () => void;
  nodes: Node[];
  edges: Edge[];
  testResult: any;
}

// 自定义边组件 - 未执行的边显示 X 标记
const SkippedEdge = ({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  markerEnd,
  data,
}: any) => {
  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const isSkipped = data?.isSkipped;

  return (
    <>
      <path
        id={id}
        style={style}
        className="react-flow__edge-path"
        d={edgePath}
        markerEnd={markerEnd}
      />
      {isSkipped && (
        <EdgeText
          x={(sourceX + targetX) / 2}
          y={(sourceY + targetY) / 2}
          className="edge-skipped-mark"
        >
          <CloseOutlined style={{ fontSize: 16, color: '#ff4d4f' }} />
        </EdgeText>
      )}
    </>
  );
};

const edgeTypes: EdgeTypes = {
  skipped: SkippedEdge,
  default: SkippedEdge,
};

// 自定义测试结果节点组件
const TestResultNode = ({ data, selected }: { data: any; selected?: boolean }) => {
  const { label, testResult, nodeType, isSkipped } = data;

  const getStatusIcon = () => {
    if (isSkipped) {
      return <CloseCircleOutlined className="status-icon skipped" />;
    }
    if (!testResult) {
      return <ClockCircleOutlined className="status-icon pending" />;
    }
    if (testResult.success) {
      return <CheckCircleOutlined className="status-icon success" />;
    }
    return <CloseCircleOutlined className="status-icon error" />;
  };

  const getStatusBadge = () => {
    if (isSkipped) return <Tag color="default">未执行</Tag>;
    if (!testResult) return <Tag color="default">未执行</Tag>;
    if (testResult.success) {
      return <Tag color="success">成功</Tag>;
    }
    return <Tag color="error">失败</Tag>;
  };

  return (
    <div className={`test-result-node ${
      isSkipped ? 'skipped' :
      testResult?.success ? 'success' :
      testResult?.success === false ? 'error' : 'pending'
    } ${selected ? 'selected' : ''}`}>
      {/* 连接点 - Input Handle */}
      <Handle
        type="target"
        position={Position.Left}
        className={`custom-handle ${isSkipped ? 'handle-skipped' : ''}`}
      />

      <div className="node-header">
        {getStatusIcon()}
        <span className="node-label">{label}</span>
        <span className="node-type-tag">{nodeType}</span>
      </div>

      {testResult && (
        <div className="node-body">
          <div className="node-status">{getStatusBadge()}</div>

          {testResult.executionTime !== undefined && (
            <div className="node-metric">
              <ClockCircleOutlined />
              <span>{testResult.executionTime}ms</span>
            </div>
          )}

          {testResult.data?.detection_count !== undefined && (
            <div className="node-metric">
              <span>检测: {testResult.data.detection_count} 个</span>
            </div>
          )}

          {testResult.data?.message && (
            <div className="node-message">{testResult.data.message}</div>
          )}
        </div>
      )}

      {isSkipped && (
        <div className="node-skipped-badge">
          <CloseCircleOutlined /> 未执行
        </div>
      )}

      {testResult?.error && (
        <div className="node-error">{testResult.error}</div>
      )}

      {/* 连接点 - Output Handle */}
      <Handle
        type="source"
        position={Position.Right}
        className={`custom-handle ${isSkipped ? 'handle-skipped' : ''}`}
      />
    </div>
  );
};

const nodeTypes: NodeTypes = {
  custom: TestResultNode,
};

const TestResultModal: React.FC<TestResultModalProps> = ({
  visible,
  onClose,
  nodes,
  edges,
  testResult,
}) => {
  // 构建带测试结果的节点数据
  const testNodes = useMemo(() => {
    const resultMap = new Map(
      testResult?.nodes?.map((n: any) => [n.nodeId, n]) || []
    );

    // 获取已执行的节点 ID
    const executedNodeIds = new Set(testResult?.nodes?.map((n: any) => n.nodeId) || []);

    console.log('📊 TestResultModal nodes 原始数据:', nodes);
    console.log('📊 测试结果映射:', resultMap);
    console.log('📊 已执行的节点:', executedNodeIds);

    const mappedNodes = nodes.map(node => {
      const isExecuted = executedNodeIds.has(node.id);

      return {
        ...node,
        type: 'custom',
        data: {
          ...node.data,
          label: node.data?.label || node.id,
          nodeType: node.type || node.data?.type || 'unknown',
          testResult: resultMap.get(node.id) || null,
          isSkipped: !isExecuted,
        },
      };
    });

    console.log('📊 TestResultModal nodes 映射后:', mappedNodes);
    return mappedNodes;
  }, [nodes, testResult]);

  // 构建高亮路径的边数据
  const testEdges = useMemo(() => {
    const executedNodeIds = new Set(testResult?.nodes?.map((n: any) => n.nodeId) || []);

    console.log('📊 TestResultModal edges 原始数据:', edges);
    console.log('📊 执行的节点 ID:', executedNodeIds);

    const mappedEdges = edges.map(edge => {
      const sourceExecuted = executedNodeIds.has(edge.source);
      const targetExecuted = executedNodeIds.has(edge.target);
      const isExecuted = sourceExecuted && targetExecuted;
      const isSkipped = !sourceExecuted || !targetExecuted;

      // 清理 handle 配置，使用默认位置
      const cleanedEdge = {
        ...edge,
        sourceHandle: edge.sourceHandle || undefined,
        targetHandle: edge.targetHandle || undefined,
        // 确保有 markerEnd 属性，使用 MarkerType 枚举
        markerEnd: edge.markerEnd || {
          type: MarkerType.ArrowClosed,
          width: 20,
          height: 20,
        },
        // 添加数据标记
        data: {
          ...edge.data,
          isSkipped,
        },
        // 添加样式类名
        className: isSkipped ? 'edge-skipped' : 'edge-executed',
        // 添加动画（只对已执行的边）
        animated: isExecuted,
        // 确保边的样式属性
        style: {
          ...edge.style,
          strokeWidth: isExecuted ? 3 : 2,
        },
        // 使用自定义边类型
        type: isSkipped ? 'skipped' : undefined,
      };

      console.log('边配置:', {
        source: edge.source,
        target: edge.target,
        sourceExecuted,
        targetExecuted,
        isExecuted,
        isSkipped,
        className: cleanedEdge.className,
      });

      return cleanedEdge;
    });

    console.log('📊 TestResultModal edges 映射后:', mappedEdges);
    return mappedEdges;
  }, [edges, testResult]);

  // 选中的节点详情
  const [selectedNode, setSelectedNode] = React.useState<Node | null>(null);

  const handleNodeClick = (_: React.MouseEvent, node: Node) => {
    setSelectedNode(node);
  };

  return (
    <Modal
      open={visible}
      onCancel={onClose}
      footer={null}
      width="90vw"
      style={{ top: 20, paddingBottom: 0 }}
      styles={{ body: { height: 'calc(100vh - 200px)', padding: 0 } }}
      className="test-result-modal"
      title={
        <div className="modal-title">
          <span>工作流测试结果</span>
          {testResult && (
            <Space size="large" style={{ marginLeft: 24 }}>
              <span className="title-metric">
                总耗时: <strong>{testResult.totalTime}ms</strong>
              </span>
              <span className="title-metric">
                执行节点: <strong>{testResult.nodes?.length || 0}</strong> 个
              </span>
              <span className={`title-status ${testResult.success ? 'success' : 'error'}`}>
                {testResult.success ? '测试通过' : '测试失败'}
              </span>
            </Space>
          )}
        </div>
      }
    >
      <div className="test-result-layout">
        {/* 流程图区域 */}
        <div className="flow-container">
          <ReactFlow
            nodes={testNodes}
            edges={testEdges}
            onNodeClick={handleNodeClick}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            className="test-result-flow"
          >
            <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
            <Controls />
            <MiniMap
              nodeColor={(node) => {
                const testResult = node.data?.testResult;
                const isSkipped = node.data?.isSkipped;
                if (isSkipped) return '#d9d9d9';
                if (!testResult) return '#d9d9d9';
                if (testResult.success) return '#52c41a';
                return '#ff4d4f';
              }}
            />
          </ReactFlow>
        </div>

        {/* 节点详情面板 */}
        {selectedNode && (
          <div className="node-detail-panel">
            <div className="detail-header">
              <h3>节点详情</h3>
            </div>
            <div className="detail-content">
              <Descriptions column={1} bordered size="small">
                <Descriptions.Item label="节点名称">
                  {selectedNode.data?.label}
                </Descriptions.Item>
                <Descriptions.Item label="节点类型">
                  <Tag>{selectedNode.data?.nodeType}</Tag>
                </Descriptions.Item>

                {selectedNode.data?.testResult ? (
                  <>
                    <Descriptions.Item label="执行状态">
                      {selectedNode.data.testResult.success ? (
                        <Tag color="success" icon={<CheckCircleOutlined />}>成功</Tag>
                      ) : (
                        <Tag color="error" icon={<CloseCircleOutlined />}>失败</Tag>
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label="执行耗时">
                      {selectedNode.data.testResult.executionTime} ms
                    </Descriptions.Item>

                    {selectedNode.data.testResult.error ? (
                      <Descriptions.Item label="错误信息">
                        <span style={{ color: '#ff4d4f' }}>
                          {selectedNode.data.testResult.error}
                        </span>
                      </Descriptions.Item>
                    ) : (
                      <>
                        {selectedNode.data.testResult.data?.message && (
                          <Descriptions.Item label="执行消息">
                            {selectedNode.data.testResult.data.message}
                          </Descriptions.Item>
                        )}

                        {selectedNode.data.testResult.data?.detection_count !== undefined && (
                          <Descriptions.Item label="检测数量">
                            <Tag color="blue">
                              {selectedNode.data.testResult.data.detection_count} 个目标
                            </Tag>
                          </Descriptions.Item>
                        )}

                        {selectedNode.data.testResult.data?.detections && (
                          <Descriptions.Item label="检测结果">
                            <Space direction="vertical" size="small" style={{ width: '100%' }}>
                              {selectedNode.data.testResult.data.detections.map((det: any, i: number) => (
                                <div key={i} className="detection-item">
                                  <Tag color="blue">{det.label}</Tag>
                                  <span className="confidence">
                                    置信度: {(det.confidence * 100).toFixed(1)}%
                                  </span>
                                </div>
                              ))}
                            </Space>
                          </Descriptions.Item>
                        )}

                        {selectedNode.data.testResult.data?.result_image && (
                          <Descriptions.Item label="结果图片">
                            <Image
                              src={selectedNode.data.testResult.data.result_image}
                              alt="检测结果"
                              style={{ maxWidth: '100%', borderRadius: 8 }}
                            />
                          </Descriptions.Item>
                        )}
                      </>
                    )}
                  </>
                ) : (
                  <Descriptions.Item label="执行状态">
                    <Tag color="default">未执行</Tag>
                  </Descriptions.Item>
                )}
              </Descriptions>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
};

export default TestResultModal;
