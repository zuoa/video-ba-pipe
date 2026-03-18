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
  // 保存上一个节点的ID，用于检测节点切换
  const lastNodeIdRef = useRef<string | undefined>(node?.id);

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
          const matchingSource = videoSources.find(s => String(s.id) === String(sourceId));
          if (matchingSource) {
            // 使用匹配到的源的id，确保类型一致
            formValues.videoSourceId = matchingSource.id;
            console.log('视频源匹配成功:', {
              nodeSourceId: sourceId,
              nodeSourceType: typeof sourceId,
              matchedSourceId: matchingSource.id,
              matchedSourceType: typeof matchingSource.id,
              matchedSourceName: matchingSource.name
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
      } else if (nodeType === 'function') {
        // 从 config 中读取函数配置
        const config = node.data?.config || {};
        formValues.functionName = config.function_name || 'area_ratio';
        formValues.threshold = config.threshold ?? 0.7;
        formValues.operator = config.operator || 'less_than';
        formValues.dimension = config.dimension || 'height';
        if (config.input_a?.class_filter) {
          formValues.classFilterA = config.input_a.class_filter.join(',');
        }
        if (config.input_b?.class_filter) {
          formValues.classFilterB = config.input_b.class_filter.join(',');
        }
      } else if (nodeType === 'condition') {
        formValues.targetCount = node.data.targetCount || node.data.target_count || 1;
        formValues.comparisonType = node.data.comparisonType || node.data.comparison_type || '>=';
      } else if (nodeType === 'roi') {
        formValues.roiMode = node.data.roiMode || 'postFilter';
      } else if (nodeType === 'alert') {
        console.log('🔍 Alert节点回显 - node.data:', node.data);
        console.log('🔍 messageFormat 值:', node.data.messageFormat);
        formValues.alertLevel = node.data.alertLevel || 'info';
        formValues.alertMessage = node.data.alertMessage || '检测到目标';
        formValues.alertType = node.data.alertType || 'detection';
        formValues.messageFormat = node.data.messageFormat || 'detailed';
        console.log('✅ formValues.messageFormat 设置为:', formValues.messageFormat);

        // 读取触发条件配置
        const triggerCondition = node.data.triggerCondition;
        if (triggerCondition) {
          formValues.triggerConditionEnable = triggerCondition.enable !== undefined ? triggerCondition.enable : true;
          formValues.triggerConditionWindowSize = triggerCondition.window_size || 30;
          formValues.triggerConditionMode = triggerCondition.mode || 'ratio';
          formValues.triggerConditionThreshold = triggerCondition.threshold !== undefined
            ? triggerCondition.threshold
            : 0.3;
        } else {
          // 默认禁用触发条件
          formValues.triggerConditionEnable = false;
          formValues.triggerConditionWindowSize = 30;
          formValues.triggerConditionMode = 'ratio';
          formValues.triggerConditionThreshold = 0.3;
        }

        // 读取抑制配置
        const suppression = node.data.suppression;
        if (suppression) {
          formValues.suppressionEnable = suppression.enable !== undefined ? suppression.enable : true;
          formValues.suppressionSeconds = suppression.seconds || 60;
        } else {
          // 默认禁用抑制
          formValues.suppressionEnable = false;
          formValues.suppressionSeconds = 60;
        }
      } else if (nodeType === 'record') {
        formValues.recordDuration = node.data.recordDuration || 10;
      }

      console.log('📝 [PropertyPanel] 即将设置表单值，messageFormat:', formValues.messageFormat);

      // 先重置表单，清除旧值
      form.resetFields();

      // 然后设置新值
      form.setFieldsValue(formValues);

      console.log('✅ 表单初始化完成');

      // 验证表单值是否正确设置
      setTimeout(() => {
        const currentValues = form.getFieldsValue();
        console.log('🔍 [PropertyPanel] 验证表单值，messageFormat:', currentValues.messageFormat);
      }, 100);
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
        const selectedSource = videoSources.find(s => String(s.id) === String(values.videoSourceId));
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

        updatedData.config = config;

        delete updatedData.intervalSeconds;
        delete updatedData.runtimeTimeout;
        delete updatedData.memoryLimitMb;
        delete updatedData.labelName;
        delete updatedData.labelColor;
      } else if (nodeType === 'alert') {
        // Alert 节点：保存触发条件和抑制配置

        // 保存消息格式
        console.log('📝 Alert节点保存 messageFormat:', values.messageFormat);
        updatedData.messageFormat = values.messageFormat || 'detailed';
        console.log('✅ updatedData.messageFormat 已设置为:', updatedData.messageFormat);

        // 保存触发条件配置
        if (values.triggerConditionEnable) {
          updatedData.triggerCondition = {
            enable: true,
            window_size: values.triggerConditionWindowSize || 30,
            mode: values.triggerConditionMode || 'ratio',
            threshold: values.triggerConditionThreshold !== undefined
              ? values.triggerConditionThreshold
              : 0.3,
          };
        } else {
          updatedData.triggerCondition = {
            enable: false,
          };
        }

        // 保存抑制配置
        if (values.suppressionEnable) {
          updatedData.suppression = {
            enable: true,
            seconds: values.suppressionSeconds || 60,
          };
        } else {
          updatedData.suppression = {
            enable: false,
          };
        }

        // 清理临时字段
        delete updatedData.triggerConditionEnable;
        delete updatedData.triggerConditionWindowSize;
        delete updatedData.triggerConditionMode;
        delete updatedData.triggerConditionThreshold;
        delete updatedData.suppressionEnable;
        delete updatedData.suppressionSeconds;
      } else if (nodeType === 'function') {
        // 自动从连线中识别上游算法节点
        const upstreamAlgorithmNodes = edges
          .filter(edge => edge.target === node.id)
          .map(edge => {
            const sourceNode = nodes.find(n => n.id === edge.source);
            return sourceNode;
          })
          .filter(n => n && (n.data?.type === 'algorithm' || n.type === 'algorithm'));

        const config = node.data?.config || {};

        config.function_name = values.functionName;

        // 自动保存上游节点ID
        if (upstreamAlgorithmNodes.length > 0) {
          config.input_a = {
            node_id: upstreamAlgorithmNodes[0].id,
            class_filter: values.classFilterA ? values.classFilterA.split(',').map((n: string) => parseInt(n.trim())) : []
          };
        }

        config.threshold = values.threshold;
        config.operator = values.operator;

        // 单输入函数列表
        const singleInputFunctions = [
          'height_ratio_frame',
          'width_ratio_frame',
          'area_ratio_frame',
          'size_absolute'
        ];

        // 双输入函数且有两个上游节点时，设置 input_b
        if (!singleInputFunctions.includes(values.functionName) && upstreamAlgorithmNodes.length > 1) {
          config.input_b = {
            node_id: upstreamAlgorithmNodes[1].id,
            class_filter: values.classFilterB ? values.classFilterB.split(',').map((n: string) => parseInt(n.trim())) : []
          };
        }

        // size_absolute 函数需要保存 dimension
        if (values.functionName === 'size_absolute') {
          config.dimension = values.dimension || 'height';
        }

        updatedData.config = config;

        // 保存所有上游节点ID列表到 data 中
        updatedData.input_nodes = upstreamAlgorithmNodes.map(n => n.id);

        // 清理临时字段
        delete updatedData.inputNodeA;
        delete updatedData.inputNodeB;
        delete updatedData.classFilterA;
        delete updatedData.classFilterB;
      } else if (nodeType === 'condition') {
        // Condition 节点：保存条件配置
        updatedData.targetCount = values.targetCount || 1;
        updatedData.comparisonType = values.comparisonType || '>=';
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
        const currentSource = videoSources.find(s => String(s.id) === String(currentSourceId));

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
          </>
        );

      case 'condition':
        return (
          <>
            <div className="info-box" style={{ marginBottom: 16 }}>
              <InfoCircleOutlined />
              <span>条件节点判断检测数量是否满足条件，通过 yes/no 端口控制后续节点执行</span>
            </div>

            <Form.Item
              label="比较类型"
              name="comparisonType"
              extra="选择如何比较检测数量与阈值"
            >
              <Select>
                <Option value=">=">至少N个 (≥)</Option>
                <Option value="==">正好N个 (=)</Option>
              </Select>
            </Form.Item>

            <Form.Item
              label="数量阈值"
              name="targetCount"
              extra="检测数量的比较阈值"
              rules={[
                { required: true, message: '请输入数量阈值' },
                { type: 'number', min: 1, max: 1000, message: '请输入1-1000之间的数字' }
              ]}
            >
              <InputNumber
                min={1}
                max={1000}
                step={1}
                style={{ width: '100%' }}
                placeholder="输入数量阈值"
              />
            </Form.Item>

            <div className="info-box" style={{ background: '#f6ffed', borderColor: '#b7eb8f', color: '#52c41a' }}>
              <InfoCircleOutlined />
              <span>
                示例：至少3人 → 连接 Alert 到 yes 端口；少于3人 → 连接 Alert 到 no 端口
              </span>
            </div>
          </>
        );

      case 'function':
        // 自动识别上游算法节点
        const getUpstreamAlgorithmNodes = () => {
          const upstreamNodes = edges
            .filter(edge => edge.target === node.id)
            .map(edge => {
              const sourceNode = nodes.find(n => n.id === edge.source);
              return sourceNode;
            })
            .filter(n => n && (n.data?.type === 'algorithm' || n.type === 'algorithm'));

          return upstreamNodes;
        };

        const upstreamNodes = getUpstreamAlgorithmNodes();

        return (
          <>
            {/* 输入配置 */}
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">输入配置（自动识别）</span>
              </div>

              {upstreamNodes.length === 0 ? (
                <div className="info-box" style={{ background: '#fff7e6', borderColor: '#ffd591', color: '#d46b08' }}>
                  <InfoCircleOutlined />
                  <span>请先从上游算法节点连线到当前函数节点</span>
                </div>
              ) : (
                <>
                  <div className="info-box" style={{ background: '#f6ffed', borderColor: '#b7eb8f', color: '#52c41a', marginBottom: 12 }}>
                    <InfoCircleOutlined />
                    <span>已自动识别 {upstreamNodes.length} 个上游算法节点</span>
                  </div>

                  {upstreamNodes.map((upstreamNode, index) => {
                    const letter = index === 0 ? 'A' : 'B';
                    return (
                      <div key={upstreamNode.id} style={{ marginBottom: 16 }}>
                        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: '#262626' }}>
                          输入节点{letter}：{upstreamNode.data?.label || upstreamNode.id}
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                          <Tag color="blue">{upstreamNode.id}</Tag>
                          <span style={{ fontSize: 12, color: '#8c8c8c' }}>
                            {upstreamNode.data?.dataId || 'N/A'}
                          </span>
                        </div>

                        <Form.Item
                          label={`类别过滤${letter}`}
                          name={index === 0 ? 'classFilterA' : 'classFilterB'}
                          style={{ marginBottom: 0 }}
                        >
                          <Input placeholder={`如: 0,1,2 (留空表示全部类别)`} />
                        </Form.Item>

                        {index < upstreamNodes.length - 1 && <div className="form-divider" style={{ margin: '12px 0' }} />}
                      </div>
                    );
                  })}

                  {/* 单输入函数但连接了多个节点时的警告 */}
                  <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.functionName !== currentValues.functionName}>
                    {({ getFieldValue }) => {
                      const functionName = getFieldValue('functionName') || 'area_ratio';
                      const singleInputFunctions = [
                        'height_ratio_frame',
                        'width_ratio_frame',
                        'area_ratio_frame',
                        'size_absolute'
                      ];

                      if (singleInputFunctions.includes(functionName) && upstreamNodes.length > 1) {
                        return (
                          <div className="info-box" style={{ marginTop: 12, background: '#fff7e6', borderColor: '#ffd591', color: '#d46b08' }}>
                            <InfoCircleOutlined />
                            <span>此函数为单输入模式，将只使用第一个输入节点</span>
                          </div>
                        );
                      }
                      return null;
                    }}
                  </Form.Item>

                  {/* 双输入函数但只连接了一个节点时的警告 */}
                  <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.functionName !== currentValues.functionName}>
                    {({ getFieldValue }) => {
                      const functionName = getFieldValue('functionName') || 'area_ratio';
                      const singleInputFunctions = [
                        'height_ratio_frame',
                        'width_ratio_frame',
                        'area_ratio_frame',
                        'size_absolute'
                      ];

                      if (!singleInputFunctions.includes(functionName) && upstreamNodes.length < 2) {
                        return (
                          <div className="info-box" style={{ marginTop: 12, background: '#fff7e6', borderColor: '#ffd591', color: '#d46b08' }}>
                            <InfoCircleOutlined />
                            <span>此函数需要2个输入节点，请再连接一个算法节点</span>
                          </div>
                        );
                      }
                      return null;
                    }}
                  </Form.Item>
                </>
              )}
            </div>

            <div className="form-divider" />

            {/* 计算函数与判定条件 */}
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">计算函数与判定条件</span>
              </div>

              <Form.Item
                label="计算函数"
                name="functionName"
              >
                <Select>
                  <Option value="area_ratio">面积比（双输入）</Option>
                  <Option value="height_ratio">高度比（双输入）</Option>
                  <Option value="width_ratio">宽度比（双输入）</Option>
                  <Option value="iou_check">IOU检查（双输入）</Option>
                  <Option value="distance_check">距离检查（双输入）</Option>
                  <Option value="height_ratio_frame">高度占图片比例（单输入）</Option>
                  <Option value="width_ratio_frame">宽度占图片比例（单输入）</Option>
                  <Option value="area_ratio_frame">面积占图片比例（单输入）</Option>
                  <Option value="size_absolute">绝对尺寸检测（单输入）</Option>
                </Select>
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

              <Form.Item
                label="阈值"
                name="threshold"
                extra={
                  <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.functionName !== currentValues.functionName}>
                    {({ getFieldValue }) => {
                      const functionName = getFieldValue('functionName') || 'area_ratio';
                      const singleInputFunctions = [
                        'height_ratio_frame',
                        'width_ratio_frame',
                        'area_ratio_frame',
                        'size_absolute'
                      ];

                      if (singleInputFunctions.includes(functionName)) {
                        return '对于比例函数，值为0-1之间；对于绝对尺寸函数，值为像素值';
                      }
                      return '值为0-1之间的小数，如0.7表示70%';
                    }}
                  </Form.Item>
                }
              >
                <InputNumber
                  min={0}
                  max={1000}
                  step={0.01}
                  style={{ width: '100%' }}
                />
              </Form.Item>

              {/* 仅 size_absolute 函数显示 dimension 选择器 */}
              <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.functionName !== currentValues.functionName}>
                {({ getFieldValue }) => {
                  const functionName = getFieldValue('functionName') || 'area_ratio';

                  if (functionName === 'size_absolute') {
                    return (
                      <Form.Item
                        label="检测维度"
                        name="dimension"
                        extra="选择要检测的尺寸维度"
                      >
                        <Select>
                          <Option value="height">高度</Option>
                          <Option value="width">宽度</Option>
                          <Option value="area">面积</Option>
                        </Select>
                      </Form.Item>
                    );
                  }

                  return null;
                }}
              </Form.Item>
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
              return videoSources.find(s => String(s.id) === String(videoSourceId)) || null;
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
              label="告警类型"
              name="alertType"
              extra="用于区分不同类型的告警"
            >
              <Input placeholder="例如: person, vehicle, fire" />
            </Form.Item>
            <Form.Item
              label="告警消息"
              name="alertMessage"
              extra="自定义告警消息前缀，会自动追加执行详情"
            >
              <Input placeholder="自定义告警消息" />
            </Form.Item>
            <Form.Item
              label="消息格式"
              name="messageFormat"
              extra="执行详情的展示格式"
            >
              <Select
                placeholder="请选择消息格式"
                onChange={(value) => console.log('🔄 Select onChange:', value)}
              >
                <Option value="detailed">详细格式（包含节点ID）</Option>
                <Option value="simple">简单格式（仅消息内容）</Option>
                <Option value="summary">汇总格式（按级别分组）</Option>
              </Select>
            </Form.Item>

            <div className="form-divider" />

            {/* 触发条件配置（窗口检测） */}
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">触发条件配置（窗口检测）</span>
              </div>

              <Form.Item
                label="启用窗口检测"
                name="triggerConditionEnable"
                extra="是否启用窗口检测验证，禁用时所有检测都会触发告警"
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>

              <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.triggerConditionEnable !== currentValues.triggerConditionEnable}>
                {({ getFieldValue }) => {
                  const enableTrigger = getFieldValue('triggerConditionEnable');

                  if (!enableTrigger) {
                    return (
                      <div className="info-box" style={{ marginTop: 12, background: '#f6ffed', borderColor: '#b7eb8f', color: '#52c41a' }}>
                        <InfoCircleOutlined />
                        <span>窗口检测已禁用，所有检测都将触发告警</span>
                      </div>
                    );
                  }

                  return (
                    <>
                      <Form.Item
                        label="时间窗口（秒）"
                        name="triggerConditionWindowSize"
                        extra="统计检测情况的时间窗口"
                      >
                        <InputNumber min={1} max={300} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="检测模式"
                        name="triggerConditionMode"
                      >
                        <Select>
                          <Option value="count">检测次数 (count)</Option>
                          <Option value="ratio">检测比例 (ratio)</Option>
                          <Option value="consecutive">连续检测 (consecutive)</Option>
                        </Select>
                      </Form.Item>

                      <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.triggerConditionMode !== currentValues.triggerConditionMode}>
                        {({ getFieldValue }) => {
                          const mode = getFieldValue('triggerConditionMode') || 'ratio';

                          // 根据不同模式设置不同的标签和提示
                          let label = '检测阈值';
                          let extra = '';
                          let inputType = 'number';

                          if (mode === 'ratio') {
                            label = '检测阈值（比例）';
                            extra = '0-1之间的小数，如0.3表示30%的帧检测到目标';
                            inputType = 'ratio';
                          } else if (mode === 'count') {
                            label = '检测阈值（次数）';
                            extra = '正整数，时间窗口内最少检测次数';
                            inputType = 'count';
                          } else if (mode === 'consecutive') {
                            label = '检测阈值（次数）';
                            extra = '正整数，最少连续检测次数';
                            inputType = 'count';
                          }

                          return (
                            <Form.Item
                              label={label}
                              name="triggerConditionThreshold"
                              extra={extra}
                            >
                              {inputType === 'ratio' ? (
                                <InputNumber min={0} max={1} step={0.01} style={{ width: '100%' }} />
                              ) : (
                                <InputNumber min={1} max={100} step={1} style={{ width: '100%' }} />
                              )}
                            </Form.Item>
                          );
                        }}
                      </Form.Item>
                    </>
                  );
                }}
              </Form.Item>
            </div>

            <div className="form-divider" />

            {/* 告警抑制配置（触发后冷却期） */}
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">告警抑制配置（触发后冷却期）</span>
              </div>

              <Form.Item
                label="启用告警抑制"
                name="suppressionEnable"
                extra="是否启用告警抑制，启用后触发告警的 N 秒内不会再触发"
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>

              <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.suppressionEnable !== currentValues.suppressionEnable}>
                {({ getFieldValue }) => {
                  const enableSuppression = getFieldValue('suppressionEnable');

                  if (!enableSuppression) {
                    return (
                      <div className="info-box" style={{ marginTop: 12, background: '#f6ffed', borderColor: '#b7eb8f', color: '#52c41a' }}>
                        <InfoCircleOutlined />
                        <span>告警抑制已禁用，满足触发条件时每次都会告警</span>
                      </div>
                    );
                  }

                  return (
                    <Form.Item
                      label="抑制时长（秒）"
                      name="suppressionSeconds"
                      extra="触发告警后，N秒内不会再触发告警"
                    >
                      <InputNumber min={1} max={3600} step={1} style={{ width: '100%' }} />
                    </Form.Item>
                  );
                }}
              </Form.Item>
            </div>
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
          const selectedSource = videoSources.find(s => String(s.id) === String(value));
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
