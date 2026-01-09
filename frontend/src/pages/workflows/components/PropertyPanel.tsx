import React, { useState, useEffect, useRef } from 'react';
import { Form, Input, Select, Button, Empty, Tabs, Space, Tag, Switch, InputNumber, Typography, List } from 'antd';
import {
  SettingOutlined,
  DeleteOutlined,
  InfoCircleOutlined,
  SearchOutlined,
  VideoCameraOutlined,
  EditOutlined,
} from '@ant-design/icons';
import { getNodeTypes } from './nodes';
import VideoSourceSelector from './VideoSourceSelector';
import ROIDrawer, { ROIRegion } from './ROIDrawer';
import './PropertyPanel.css';

const { TextArea } = Input;
const { Option } = Select;
const { Text } = Typography;

export interface PropertyPanelProps {
  node: any;
  videoSources: any[];
  edges?: any[];
  nodes?: any[];
  onUpdate: (data: any) => void;
  onDelete: () => void;
}

const PropertyPanel: React.FC<PropertyPanelProps> = ({
  node,
  videoSources,
  edges = [],
  nodes = [],
  onUpdate,
  onDelete,
}) => {
  const [form] = Form.useForm();
  const [activeTab, setActiveTab] = useState('basic');
  const [selectorVisible, setSelectorVisible] = useState(false);
  const [roiDrawerVisible, setRoiDrawerVisible] = useState(false);

  // 使用 useRef 而不是 useState，确保同步更新
  const isUpdatingVideoSourceRef = useRef(false);

  console.log('PropertyPanel render, node:', node);
  console.log('Available videoSources:', videoSources);
  console.log('onUpdate 函数:', onUpdate);
  console.log('onUpdate 函数名:', onUpdate.name);

  // 当 node 变化时，回显节点数据到表单
  useEffect(() => {
    if (node) {
      const nodeConfig = node.data?.config || {};
      const nodeType = node.data?.type || node.type;

      console.log('🔄 PropertyPanel useEffect 触发');
      console.log('📦 节点类型:', nodeType);
      console.log('📋 节点数据:', node.data);
      console.log('🔧 节点 config:', node.data?.config);
      console.log('🎥 videoSourceId:', node.data.videoSourceId, 'videoSourceName:', node.data.videoSourceName);
      console.log('🚫 isUpdatingVideoSourceRef.current:', isUpdatingVideoSourceRef.current);

      // 如果正在更新视频源，不要覆盖表单值
      if (isUpdatingVideoSourceRef.current) {
        console.log('⏸️ 跳过表单初始化，正在更新视频源');
        isUpdatingVideoSourceRef.current = false; // 重置标志
        return;
      }

      // 获取当前表单值，检查表单是否已经有值
      const currentFormValues = form.getFieldsValue();
      console.log('📝 当前表单值:', currentFormValues);

      // 对于视频源节点，如果表单中已经有 videoSourceId，且与 node.data 中的一致，说明是同一次渲染，不需要重新初始化
      if ((nodeType === 'videoSource' || nodeType === 'source') && currentFormValues.videoSourceId !== undefined) {
        if (currentFormValues.videoSourceId == node.data.videoSourceId) {
          console.log('⏸️ 表单值与节点数据一致，跳过重复初始化');
          return;
        }
      }

      // 根据节点类型设置不同的表单值
      const formValues: any = {
        label: node.data.label,
        description: node.data.description || '',
      };

      // 根据节点类型回显特定字段
      if (nodeType === 'videoSource' || nodeType === 'source') {
        // 确保 videoSourceId 的类型与 videoSources 中的 id 类型一致
        const sourceId = node.data.videoSourceId;
        if (sourceId !== undefined && sourceId !== null) {
          // 找到匹配的视频源来确认类型
          const matchingSource = videoSources.find(s => s.id == sourceId); // 使用 == 宽松匹配
          if (matchingSource) {
            // 使用匹配到的源的id，确保类型一致
            formValues.videoSourceId = matchingSource.id;
            console.log('视频源匹配成功:', {
              节点中的值: sourceId,
              类型: typeof sourceId,
              匹配源的id: matchingSource.id,
              类型: typeof matchingSource.id,
              视频源名称: matchingSource.name
            });
          } else {
            console.warn('未找到匹配的视频源:', sourceId, '可用视频源:', videoSources);
            formValues.videoSourceId = sourceId;
          }
        }
      } else if (nodeType === 'algorithm') {
        formValues.confidence = node.data.confidence || 0.5;

        // 执行配置
        formValues.intervalSeconds = nodeConfig.interval_seconds || 1;
        formValues.runtimeTimeout = nodeConfig.runtime_timeout || 30;
        formValues.memoryLimitMb = nodeConfig.memory_limit_mb || 512;
        formValues.labelName = nodeConfig.label_name || 'Object';
        formValues.labelColor = nodeConfig.label_color || '#FF0000';

        // 窗口检测配置
        const windowDetection = nodeConfig.window_detection || {};
        formValues.windowEnable = windowDetection.enable || false;
        formValues.windowSize = windowDetection.window_size || 30;
        formValues.windowMode = windowDetection.window_mode || 'ratio';
        formValues.windowThreshold = windowDetection.window_threshold !== undefined
          ? windowDetection.window_threshold
          : 0.3;
      } else if (nodeType === 'function') {
        formValues.functionName = node.data.functionName || 'area_ratio';
        formValues.inputNodeA = node.data.inputNodeA || '';
        formValues.inputNodeB = node.data.inputNodeB || '';
        formValues.classFilterA = node.data.classFilterA || '';
        formValues.classFilterB = node.data.classFilterB || '';
        formValues.threshold = node.data.threshold || 0.7;
        formValues.operator = node.data.operator || 'less_than';
      } else if (nodeType === 'condition') {
        formValues.conditionType = node.data.conditionType || 'detection';
        formValues.targetCount = node.data.targetCount || 1;
      } else if (nodeType === 'roi') {
        formValues.roiMode = node.data.roiMode || 'postFilter';
      } else if (nodeType === 'alert') {
        formValues.alertLevel = node.data.alertLevel || 'info';
        formValues.alertMessage = node.data.alertMessage || '检测到目标';
      } else if (nodeType === 'record') {
        formValues.recordDuration = node.data.recordDuration || 10;
      }

      form.setFieldsValue(formValues);
      console.log('✅ 表单初始化完成');
    }
  }, [node, node?.data, node?.id, form]); // 移除 videoSources 依赖，避免不必要的重渲染

  if (!node) {
    return (
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
    );
  }

  const handleUpdate = async () => {
    try {
      const values = await form.validateFields();

      console.log('🔧 handleUpdate - 表单验证值:', values);
      console.log('🔧 handleUpdate - 当前节点数据:', node.data);

      // 处理算法节点的窗口检测配置
      const updatedData: any = { ...values };

      // 特殊处理视频源节点：添加视频源名称和编码
      const nodeType = node.data?.type || node.type;
      if ((nodeType === 'videoSource' || nodeType === 'source') && values.videoSourceId) {
        const selectedSource = videoSources.find(s => s.id == values.videoSourceId);
        if (selectedSource) {
          // 重要：也要更新 dataId，否则会被旧数据覆盖
          updatedData.dataId = selectedSource.id;
          updatedData.videoSourceName = selectedSource.name;
          updatedData.videoSourceCode = selectedSource.source_code;
          console.log('✅ 视频源节点更新:', {
            id: selectedSource.id,
            name: selectedSource.name,
            source_code: selectedSource.source_code
          });
        } else {
          console.warn('⚠️ 未找到选中的视频源, videoSourceId:', values.videoSourceId);
        }
      }

      if (nodeType === 'algorithm') {
        const config = node.data?.config || {};

        // 保存执行配置
        config.interval_seconds = values.intervalSeconds;
        config.runtime_timeout = values.runtimeTimeout;
        config.memory_limit_mb = values.memoryLimitMb;
        config.label_name = values.labelName;
        config.label_color = values.labelColor;

        // 保存窗口检测配置
        if (values.windowEnable) {
          config.window_detection = {
            enable: true,
            window_size: values.windowSize || 30,
            window_mode: values.windowMode || 'ratio',
            window_threshold: values.windowThreshold !== undefined ? values.windowThreshold : 0.3,
          };
        } else {
          delete config.window_detection;
        }

        updatedData.config = config;

        delete updatedData.windowEnable;
        delete updatedData.windowSize;
        delete updatedData.windowMode;
        delete updatedData.windowThreshold;
        delete updatedData.intervalSeconds;
        delete updatedData.runtimeTimeout;
        delete updatedData.memoryLimitMb;
        delete updatedData.labelName;
        delete updatedData.labelColor;
      } else if (nodeType === 'function') {
        const config = node.data?.config || {};
        
        config.function_name = values.functionName;
        config.input_a = {
          node_id: values.inputNodeA,
          class_filter: values.classFilterA ? values.classFilterA.split(',').map((n: string) => parseInt(n.trim())) : []
        };
        config.input_b = {
          node_id: values.inputNodeB,
          class_filter: values.classFilterB ? values.classFilterB.split(',').map((n: string) => parseInt(n.trim())) : []
        };
        config.threshold = values.threshold;
        config.operator = values.operator;
        
        updatedData.config = config;
        updatedData.functionName = values.functionName;
        updatedData.threshold = values.threshold;
        
        const inputNodes = [];
        if (values.inputNodeA) inputNodes.push(values.inputNodeA);
        if (values.inputNodeB) inputNodes.push(values.inputNodeB);
        updatedData.input_nodes = inputNodes;
        
        delete updatedData.inputNodeA;
        delete updatedData.inputNodeB;
        delete updatedData.classFilterA;
        delete updatedData.classFilterB;
        delete updatedData.operator;
      }

      console.log('📤 准备调用onUpdate, 更新数据:', updatedData);
      onUpdate(updatedData);
    } catch (error) {
      console.error('❌ Form validation failed:', error);
    }
  };

  const getNodeConfigFields = () => {
    const nodeType = node.data?.type || node.type;
    console.log('getNodeConfigFields - 节点类型:', nodeType);
    console.log('当前 videoSourceId:', node.data.videoSourceId, '类型:', typeof node.data.videoSourceId);
    console.log('可用视频源:', videoSources);

    switch (nodeType) {
      case 'videoSource':
      case 'source':
        // 获取当前选中的视频源
        const currentSourceId = node.data.videoSourceId;
        const currentSource = videoSources.find(s => s.id == currentSourceId);

        console.log('渲染视频源配置 -', {
          currentSourceId,
          currentSource: currentSource ? { name: currentSource.name, id: currentSource.id } : null,
          nodeDataKeys: Object.keys(node.data),
        });

        return (
          <>
            <div className="video-source-selector-trigger">
              {currentSource ? (
                <div className="current-source-card">
                  <div className="source-card-header">
                    <Space size="small">
                      <VideoCameraOutlined style={{ fontSize: 16, color: '#1890ff' }} />
                      <Text strong style={{ fontSize: 15 }}>
                        {currentSource.name}
                      </Text>
                    </Space>
                    <Button
                      type="primary"
                      size="small"
                      icon={<SearchOutlined />}
                      onClick={() => setSelectorVisible(true)}
                    >
                      重新选择
                    </Button>
                  </div>

                  <div className="source-card-details">
                    <div className="detail-item">
                      <span className="detail-label">编码:</span>
                      <span className="detail-value">{currentSource.source_code || '-'}</span>
                    </div>
                    <div className="detail-item">
                      <span className="detail-label">ID:</span>
                      <span className="detail-value">{currentSource.id}</span>
                    </div>
                    {currentSource.decoder_type && (
                      <div className="detail-item">
                        <span className="detail-label">解码器:</span>
                        <span className="detail-value">{currentSource.decoder_type}</span>
                      </div>
                    )}
                    {currentSource.url && (
                      <div className="detail-item">
                        <span className="detail-label">URL:</span>
                        <span className="detail-value url-text">{currentSource.url}</span>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <Button
                  type="dashed"
                  block
                  size="large"
                  icon={<SearchOutlined />}
                  onClick={() => setSelectorVisible(true)}
                  style={{ height: 60, fontSize: 14 }}
                >
                  点击选择视频源
                </Button>
              )}
            </div>

            {/* 隐藏的表单项，用于验证和提交 */}
            <Form.Item
              name="videoSourceId"
              rules={[{ required: true, message: '请选择视频源' }]}
              hidden
            >
              <Input />
            </Form.Item>

            {videoSources.length === 0 && (
              <div className="info-box">
                <InfoCircleOutlined />
                <span>暂无可用视频源，请先在视频源管理中添加</span>
              </div>
            )}

            {currentSourceId && !currentSource && (
              <div className="info-box" style={{ background: '#fff7e6', borderColor: '#ffd591', color: '#d46b08' }}>
                <InfoCircleOutlined />
                <span>原视频源 (ID: {currentSourceId}) 不存在，请重新选择</span>
              </div>
            )}
          </>
        );

      case 'algorithm':
        return (
          <>
            <Form.Item
              label="置信度阈值"
              name="confidence"
            >
              <Select>
                <Option value={0.3}>0.3 (低)</Option>
                <Option value={0.5}>0.5 (中)</Option>
                <Option value={0.7}>0.7 (高)</Option>
                <Option value={0.9}>0.9 (极高)</Option>
              </Select>
            </Form.Item>

            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">执行配置</span>
              </div>

              <Form.Item
                label="检测间隔（秒）"
                name="intervalSeconds"
                extra="每N秒执行一次检测，1表示每帧都检测"
              >
                <InputNumber min={0.1} max={60} step={0.1} style={{ width: '100%' }} />
              </Form.Item>

              <Form.Item
                label="运行超时（秒）"
                name="runtimeTimeout"
                extra="单次检测最大执行时间"
              >
                <InputNumber min={1} max={300} style={{ width: '100%' }} />
              </Form.Item>

              <Form.Item
                label="内存限制（MB）"
                name="memoryLimitMb"
                extra="算法运行最大内存使用"
              >
                <InputNumber min={64} max={4096} step={64} style={{ width: '100%' }} />
              </Form.Item>

              <Form.Item
                label="标签名称"
                name="labelName"
                extra="检测结果中显示的标签名称"
              >
                <Input placeholder="例如: Person" />
              </Form.Item>

              <Form.Item
                label="标签颜色"
                name="labelColor"
              >
                <Input type="color" style={{ width: 100 }} />
              </Form.Item>
            </div>

            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">时间窗口检测（误报抑制）</span>
              </div>

              <Form.Item
                label="启用窗口检测"
                name="windowEnable"
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>

              <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.windowEnable !== currentValues.windowEnable}>
                {({ getFieldValue }) => {
                  const windowEnable = getFieldValue('windowEnable');
                  if (!windowEnable) return null;

                  return (
                    <div className="window-config-fields">
                      <Form.Item
                        label="窗口大小（秒）"
                        name="windowSize"
                      >
                        <InputNumber min={1} max={300} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="检测模式"
                        name="windowMode"
                      >
                        <Select>
                          <Option value="count">检测次数 (count)</Option>
                          <Option value="ratio">检测比例 (ratio)</Option>
                          <Option value="consecutive">连续检测 (consecutive)</Option>
                        </Select>
                      </Form.Item>

                      <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.windowMode !== currentValues.windowMode}>
                        {({ getFieldValue }) => {
                          const windowMode = getFieldValue('windowMode') || 'ratio';
                          return (
                            <Form.Item
                              label={windowMode === 'ratio' ? '检测阈值（比例）' : '检测阈值（次数）'}
                              name="windowThreshold"
                              extra={windowMode === 'ratio' ? '0-1之间的小数，如0.3表示30%' : '正整数，最少检测次数'}
                            >
                              {windowMode === 'ratio' ? (
                                <InputNumber min={0} max={1} step={0.01} style={{ width: '100%' }} />
                              ) : (
                                <InputNumber min={1} max={100} step={1} style={{ width: '100%' }} />
                              )}
                            </Form.Item>
                          );
                        }}
                      </Form.Item>
                    </div>
                  );
                }}
              </Form.Item>
            </div>
          </>
        );

      case 'condition':
        return (
          <>
            <Form.Item
              label="条件类型"
              name="conditionType"
            >
              <Select>
                <Option value="detection">检测到目标</Option>
                <Option value="noDetection">未检测到目标</Option>
                <Option value="count">数量达到</Option>
              </Select>
            </Form.Item>
            <Form.Item
              label="目标数量"
              name="targetCount"
            >
              <Select>
                <Option value={1}>1 个</Option>
                <Option value={2}>2 个</Option>
                <Option value={3}>3 个</Option>
                <Option value={5}>5 个</Option>
                <Option value={10}>10 个</Option>
              </Select>
            </Form.Item>
          </>
        );

      case 'function':
        return (
          <>
            <Form.Item
              label="计算函数"
              name="functionName"
            >
              <Select>
                <Option value="area_ratio">面积比</Option>
                <Option value="height_ratio">高度比</Option>
                <Option value="width_ratio">宽度比</Option>
                <Option value="iou_check">IOU检查</Option>
                <Option value="distance_check">距离检查</Option>
              </Select>
            </Form.Item>
            
            <div className="form-divider" />
            
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">输入配置</span>
              </div>
              
              <Form.Item
                label="输入节点A"
                name="inputNodeA"
                rules={[{ required: true, message: '请输入节点ID' }]}
              >
                <Input placeholder="如: algo1" />
              </Form.Item>
              
              <Form.Item
                label="类别过滤A"
                name="classFilterA"
              >
                <Input placeholder="如: 0,1,2 (留空表示全部)" />
              </Form.Item>
              
              <Form.Item
                label="输入节点B"
                name="inputNodeB"
                rules={[{ required: true, message: '请输入节点ID' }]}
              >
                <Input placeholder="如: algo2" />
              </Form.Item>
              
              <Form.Item
                label="类别过滤B"
                name="classFilterB"
              >
                <Input placeholder="如: 5,7 (留空表示全部)" />
              </Form.Item>
            </div>
            
            <div className="form-divider" />
            
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">判定条件</span>
              </div>
              
              <Form.Item
                label="阈值"
                name="threshold"
              >
                <InputNumber
                  min={0}
                  max={1000}
                  step={0.1}
                  style={{ width: '100%' }}
                />
              </Form.Item>
              
              <Form.Item
                label="运算符"
                name="operator"
              >
                <Select>
                  <Option value="less_than">小于</Option>
                  <Option value="greater_than">大于</Option>
                  <Option value="equal">等于</Option>
                </Select>
              </Form.Item>
            </div>
            
            <div className="info-box">
              <InfoCircleOutlined />
              <span>节点ID可在画布中查看节点属性获取</span>
            </div>
          </>
        );
      
      case 'roi':
        // 获取关联的视频源 - 通过 edges 找到连接的 videoSource 节点
        const getRoiVideoSource = () => {
          // 找到连接到当前 ROI 节点的输入边
          const inputEdge = edges.find(edge => edge.target === node.id);
          if (!inputEdge) return null;

          // 找到源节点
          const sourceNode = nodes.find(n => n.id === inputEdge.source);
          if (!sourceNode) return null;

          // 检查源节点是否是视频源节点
          const sourceType = sourceNode.data?.type || sourceNode.type;
          if (sourceType === 'videoSource' || sourceType === 'source') {
            const videoSourceId = sourceNode.data?.videoSourceId;
            if (videoSourceId) {
              return videoSources.find(s => s.id == videoSourceId) || null;
            }
          }

          // 如果连接的不是视频源，继续递归查找
          // 这里简化处理，返回第一个可用的视频源
          return videoSources[0] || null;
        };

        const roiVideoSource = getRoiVideoSource();
        const roiRegions = node.data.roiRegions || [];

        return (
          <>
            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">ROI 区域管理</span>
              </div>

              <div className="roi-status-box">
                <div className="roi-status-info">
                  <InfoCircleOutlined />
                  <span>
                    {roiRegions.length > 0
                      ? `已配置 ${roiRegions.length} 个区域`
                      : '未配置区域'}
                  </span>
                </div>
                <Button
                  type="primary"
                  icon={<EditOutlined />}
                  onClick={() => setRoiDrawerVisible(true)}
                  disabled={!roiVideoSource}
                >
                  {roiRegions.length > 0 ? '编辑区域' : '绘制区域'}
                </Button>
              </div>

              {!roiVideoSource && (
                <div className="info-box" style={{ marginTop: 12 }}>
                  <InfoCircleOutlined />
                  <span>请确保工作流中有可用的视频源并正确连接</span>
                </div>
              )}

              {roiVideoSource && (
                <div className="info-box" style={{
                  marginTop: 12,
                  background: 'linear-gradient(to right, #f6ffed, #fcffe6)',
                  borderColor: '#d9f7be',
                  color: '#389e0d'
                }}>
                  <InfoCircleOutlined />
                  <span>视频源: {roiVideoSource.name}</span>
                </div>
              )}

              {roiRegions.length > 0 && (
                <div className="roi-regions-list">
                  <Text strong>已配置的 ROI 区域:</Text>
                  <List
                    size="small"
                    dataSource={roiRegions}
                    renderItem={(region: ROIRegion, index: number) => (
                      <List.Item
                        key={index}
                        style={{
                          padding: '8px 12px',
                          background: '#fafafa',
                          borderRadius: 4,
                          marginTop: 8,
                          border: '1px solid #d9d9d9'
                        }}
                      >
                        <div style={{ width: '100%' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                            <Text strong>{region.name}</Text>
                            <Tag color={region.mode === 'pre_mask' ? 'blue' : 'green'}>
                              {region.mode === 'pre_mask' ? '前置掩码' : '后置过滤'}
                            </Tag>
                          </div>
                          <div style={{ fontSize: 11, color: '#8c8c8c' }}>
                            {region.polygon.length} 个顶点
                          </div>
                        </div>
                      </List.Item>
                    )}
                  />
                </div>
              )}
            </div>
          </>
        );

      case 'alert':
        return (
          <>
            <Form.Item
              label="告警级别"
              name="alertLevel"
            >
              <Select>
                <Option value="info">信息</Option>
                <Option value="warning">警告</Option>
                <Option value="error">错误</Option>
                <Option value="critical">严重</Option>
              </Select>
            </Form.Item>
            <Form.Item
              label="告警消息"
              name="alertMessage"
            >
              <Input placeholder="自定义告警消息" />
            </Form.Item>
          </>
        );

      case 'record':
        return (
          <>
            <Form.Item
              label="录像时长"
              name="recordDuration"
            >
              <Select>
                <Option value={5}>5 秒</Option>
                <Option value={10}>10 秒</Option>
                <Option value={30}>30 秒</Option>
                <Option value={60}>60 秒</Option>
              </Select>
            </Form.Item>
          </>
        );

      default:
        return null;
    }
  };

  return (
    <div className="property-panel">
      <div className="panel-header">
        <Space size="small">
          <SettingOutlined />
          <span className="panel-title">节点属性</span>
        </Space>
        <Button
          type="text"
          size="small"
          icon={<DeleteOutlined />}
          onClick={onDelete}
          className="delete-btn"
        >
          删除
        </Button>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        size="small"
        className="property-tabs"
      >
        <Tabs.TabPane tab="基本属性" key="basic">
          <Form
            key={node.id}
            form={form}
            layout="vertical"
            className="property-form"
          >
            <Form.Item
              label="节点名称"
              name="label"
              rules={[{ required: true, message: '请输入节点名称' }]}
            >
              <Input size="small" />
            </Form.Item>

            <Form.Item
              label="描述"
              name="description"
            >
              <TextArea rows={3} size="small" />
            </Form.Item>

            <div className="form-divider" />

            {getNodeConfigFields()}

            <Form.Item className="form-actions">
              <Button type="primary" block size="small" onClick={handleUpdate}>
                更新节点
              </Button>
            </Form.Item>
          </Form>
        </Tabs.TabPane>

        <Tabs.TabPane tab="节点信息" key="info">
          <div className="node-info">
            <div className="info-row">
              <span className="info-label">节点 ID:</span>
              <span className="info-value">{node.id}</span>
            </div>
            <div className="info-row">
              <span className="info-label">节点类型:</span>
              <Tag color={node.data.color}>{node.data.label}</Tag>
            </div>
            <div className="info-row">
              <span className="info-label">位置:</span>
              <span className="info-value">
                X: {Math.round(node.position.x)}, Y: {Math.round(node.position.y)}
              </span>
            </div>
          </div>
        </Tabs.TabPane>
      </Tabs>

      <VideoSourceSelector
        visible={selectorVisible}
        value={node.data.videoSourceId}
        videoSources={videoSources}
        onChange={(value) => {
          console.log('🎬 VideoSourceSelector onChange 被调用，新值:', value);

          // 查找选中的视频源
          const selectedSource = videoSources.find(s => s.id == value);
          if (!selectedSource) {
            console.warn('⚠️ 未找到选中的视频源，value:', value);
            setSelectorVisible(false);
            return;
          }

          // 获取当前表单的所有值
          const currentValues = form.getFieldsValue();
          console.log('📝 当前表单值（更新前）:', currentValues);

          // 🔑 关键：使用 ref 设置标志（同步更新，立即生效）
          isUpdatingVideoSourceRef.current = true;
          console.log('🚫 设置 isUpdatingVideoSourceRef.current = true');

          // 合并所有数据，保留其他字段
          const updatedData = {
            label: currentValues.label || node.data.label,
            description: currentValues.description || node.data.description || '',
            dataId: selectedSource.id,  // 重要：也要更新 dataId
            videoSourceId: value,
            videoSourceName: selectedSource.name,
            videoSourceCode: selectedSource.source_code,
          };

          console.log('🔄 准备更新节点数据:', updatedData);
          console.log('🎯 选中的视频源:', selectedSource);

          // 立即更新表单值，确保表单中有最新的videoSourceId
          form.setFieldsValue({
            label: updatedData.label,
            description: updatedData.description,
            videoSourceId: value
          });

          console.log('✅ 表单值已更新');
          console.log('📝 更新后的表单值:', form.getFieldsValue());

          // 调用onUpdate更新节点数据
          console.log('📤 准备调用 onUpdate，参数:', updatedData);
          console.log('🔍 调用时机检查 - isUpdatingVideoSourceRef.current:', isUpdatingVideoSourceRef.current);

          onUpdate(updatedData);
          console.log('✅ 已调用onUpdate');

          setSelectorVisible(false);
        }}
        onCancel={() => setSelectorVisible(false)}
      />

      <ROIDrawer
        visible={roiDrawerVisible}
        videoSourceId={(() => {
          // 获取关联的视频源 ID
          const inputEdge = edges.find(edge => edge.target === node.id);
          if (!inputEdge) return null;

          const sourceNode = nodes.find(n => n.id === inputEdge.source);
          if (!sourceNode) return null;

          const sourceType = sourceNode.data?.type || sourceNode.type;
          if (sourceType === 'videoSource' || sourceType === 'source') {
            return sourceNode.data?.videoSourceId || null;
          }

          return null;
        })()}
        videoSourceName={(() => {
          // 获取关联的视频源名称
          const inputEdge = edges.find(edge => edge.target === node.id);
          if (!inputEdge) return undefined;

          const sourceNode = nodes.find(n => n.id === inputEdge.source);
          if (!sourceNode) return undefined;

          const sourceType = sourceNode.data?.type || sourceNode.type;
          if (sourceType === 'videoSource' || sourceType === 'source') {
            return sourceNode.data?.videoSourceName;
          }

          return undefined;
        })()}
        sourceCode={(() => {
          // 获取关联的视频源 source_code
          const inputEdge = edges.find(edge => edge.target === node.id);
          if (!inputEdge) return undefined;

          const sourceNode = nodes.find(n => n.id === inputEdge.source);
          if (!sourceNode) return undefined;

          const sourceType = sourceNode.data?.type || sourceNode.type;
          if (sourceType === 'videoSource' || sourceType === 'source') {
            return sourceNode.data?.videoSourceCode;
          }

          return undefined;
        })()}
        initialRegions={node.data.roiRegions || []}
        onClose={() => setRoiDrawerVisible(false)}
        onSave={(regions) => {
          console.log('💾 保存 ROI 区域:', regions);
          const updatedData = {
            roiRegions: regions,
          };
          onUpdate(updatedData);
        }}
      />
    </div>
  );
};

export default PropertyPanel;
