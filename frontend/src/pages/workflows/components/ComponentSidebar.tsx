import React, { useState, useEffect } from 'react';
import { Collapse, message } from 'antd';
import {
  VideoCameraOutlined,
  BugOutlined,
  BranchesOutlined,
  EditOutlined,
  BellOutlined,
  PlusOutlined,
  AppstoreOutlined,
  FunctionOutlined,
  ApiOutlined,
  RobotOutlined,
  FileSearchOutlined,
  SendOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import { getAlgorithms } from '@/services/api';
import { getAlgorithmDefaultConfidence } from '../utils/algorithmDefaults';
import './ComponentSidebar.css';
import { createDefaultWeeklySchedule } from '../utils/timeSchedule';

export interface ComponentSidebarProps {
  onAddNode: (nodeData: any) => void;
  videoSources: any[];
  hasSourceNode?: boolean;
}

const ComponentSidebar: React.FC<ComponentSidebarProps> = ({ onAddNode, videoSources, hasSourceNode = false }) => {
  const [algorithms, setAlgorithms] = useState<any[]>([]);

  useEffect(() => {
    loadAlgorithms();
  }, []);

  const loadAlgorithms = async () => {
    try {
      const data = await getAlgorithms();
      console.log('📋 加载的算法列表:', data);
      setAlgorithms(data || []);
    } catch (error) {
      console.error('加载算法失败:', error);
    }
  };

  const handleAddNode = (item: any) => {
    console.log('➕ 添加节点:', item);
    onAddNode(item);
  };

  const items = [
    {
      key: 'videoSource',
      label: (
        <div className="collapse-item-label">
          <VideoCameraOutlined />
          <span className="category-title">视频源</span>
        </div>
      ),
      children: (
        <div className="component-list">
          <div
            className="component-item"
            onClick={() => {
              if (hasSourceNode) {
                message.warning('一个编排只允许一个视频源节点');
                return;
              }
              handleAddNode({
                type: 'videoSource',
                nodeType: 'videoSource',
                label: '视频源',
                description: '选择视频源',
                icon: <VideoCameraOutlined />,
                color: '#1890ff',
              });
            }}
            style={{ borderColor: '#1890ff' }}
          >
            <div className="component-item-inner">
              <span className="component-icon" style={{ color: '#1890ff' }}>
                <VideoCameraOutlined />
              </span>
              <div className="component-content">
                <div className="component-label">视频源</div>
                <div className="component-description">选择视频源</div>
              </div>
              <PlusOutlined className="component-add" />
            </div>
          </div>
        </div>
      ),
    },
    {
      key: 'algorithm',
      label: (
        <div className="collapse-item-label">
          <BugOutlined />
          <span className="category-title">算法</span>
        </div>
      ),
      children: (
        <div className="component-list">
          {algorithms.some(algo => algo.runtime_available !== false) ? (
            algorithms.filter(algo => algo.runtime_available !== false).map((algo, index) => (
              <div
                key={index}
                className="component-item"
                onClick={() => {
                  const defaultConfidence = getAlgorithmDefaultConfidence(algo);
                  handleAddNode({
                    type: 'algorithm',
                    nodeType: 'algorithm',
                    label: algo.name,
                    description: algo.description || '算法检测',
                    icon: <BugOutlined />,
                    color: '#52c41a',
                    algorithmId: algo.id,
                    dataId: algo.id,
                    algorithmType: algo.algorithm_type || 'script',
                    defaultConfidence: defaultConfidence,
                    // 从算法的 ext_config_json 中读取执行配置作为默认值
                    config: {
                      interval_seconds: algo.interval_seconds || 1,
                      ...(algo.algorithm_type === 'vl' ? {} : {
                        runtime_timeout: algo.runtime_timeout || 30,
                        memory_limit_mb: algo.memory_limit_mb || 512,
                      }),
                      label_name: algo.label_name || 'Object',
                      label_color: algo.label_color || '#FF0000',
                      window_detection: {
                        enable: algo.enable_window_check || false,
                        window_size: algo.window_size || 30,
                        window_mode: algo.window_mode || 'ratio',
                        window_threshold: algo.window_threshold !== undefined ? algo.window_threshold : 0.3,
                      },
                    },
                  });
                }}
                style={{ borderColor: algo.algorithm_type === 'vl' ? '#13c2c2' : algo.algorithm_type === 'ocr' ? '#1677ff' : '#52c41a' }}
              >
                <div className="component-item-inner">
                  <span className="component-icon" style={{ color: algo.algorithm_type === 'vl' ? '#08979c' : algo.algorithm_type === 'ocr' ? '#0958d9' : '#52c41a' }}>
                    {algo.algorithm_type === 'vl'
                      ? <RobotOutlined />
                      : algo.algorithm_type === 'ocr'
                        ? <FileSearchOutlined />
                        : <BugOutlined />}
                  </span>
                  <div className="component-content">
                    <div className="component-label">{algo.name}</div>
                    <div className="component-description">
                      {algo.algorithm_type === 'vl'
                        ? `VL · ${algo.vl_config?.model_name || '视觉语言模型'}`
                        : algo.algorithm_type === 'ocr'
                          ? `OCR · ${algo.ocr_config?.device || 'auto'}`
                          : (algo.description || '算法检测')}
                    </div>
                  </div>
                  <PlusOutlined className="component-add" />
                </div>
              </div>
            ))
          ) : (
            <div className="component-empty">
              暂无可用组件
            </div>
          )}
        </div>
      ),
    },
    {
      key: 'external-api',
      label: (
        <div className="collapse-item-label">
          <ApiOutlined />
          <span className="category-title">外部 API</span>
        </div>
      ),
      children: (
        <div className="component-list">
          <div
            className="component-item"
            onClick={() => handleAddNode({
              type: 'externalApi',
              nodeType: 'externalApi',
              label: '外部 API',
              description: '调用外部算法接口',
              icon: <ApiOutlined />,
              color: '#1677ff',
              config: {
                execution_mode: 'sync',
                interval_seconds: 1,
                timeout_seconds: 30,
                include_image: true,
                include_upstream_results: true,
                payload_template: {},
                output_mapping: {
                  has_detection_path: 'has_detection',
                  detections_path: 'detections',
                  metadata_path: 'metadata',
                },
              },
            })}
            style={{ borderColor: '#1677ff' }}
          >
            <div className="component-item-inner">
              <span className="component-icon" style={{ color: '#1677ff' }}>
                <ApiOutlined />
              </span>
              <div className="component-content">
                <div className="component-label">外部 API</div>
                <div className="component-description">调用外部算法接口</div>
              </div>
              <PlusOutlined className="component-add" />
            </div>
          </div>
        </div>
      ),
    },
    {
      key: 'condition',
      label: (
        <div className="collapse-item-label">
          <BranchesOutlined />
          <span className="category-title">条件分支</span>
        </div>
      ),
      children: (
        <div className="component-list">
          <div
            className="component-item"
            onClick={() => handleAddNode({
              type: 'condition',
              nodeType: 'condition',
              label: '检测条件',
              description: '是/否检测到',
              icon: <BranchesOutlined />,
              color: '#faad14',
              conditionKind: 'count',
              labels: [],
              windowSize: 10,
              direction: 'both',
              relativeThreshold: 0.5,
              absoluteThreshold: 3,
              confirmationCount: 1,
            })}
            style={{ borderColor: '#faad14' }}
          >
            <div className="component-item-inner">
              <span className="component-icon" style={{ color: '#faad14' }}>
                <BranchesOutlined />
              </span>
              <div className="component-content">
                <div className="component-label">检测条件</div>
                <div className="component-description">是/否检测到</div>
              </div>
              <PlusOutlined className="component-add" />
            </div>
          </div>
          <div
            className="component-item"
            onClick={() => handleAddNode({
              type: 'timeSchedule',
              nodeType: 'timeSchedule',
              label: '时间启用区间',
              description: '命中时段才继续',
              icon: <ClockCircleOutlined />,
              color: '#2f54eb',
              weeklySchedule: createDefaultWeeklySchedule(),
            })}
            style={{ borderColor: '#597ef7' }}
          >
            <div className="component-item-inner">
              <span className="component-icon" style={{ color: '#2f54eb' }}>
                <ClockCircleOutlined />
              </span>
              <div className="component-content">
                <div className="component-label">时间启用区间</div>
                <div className="component-description">按星期与时段控制流程</div>
              </div>
              <PlusOutlined className="component-add" />
            </div>
          </div>
        </div>
      ),
    },
    {
      key: 'function',
      label: (
        <div className="collapse-item-label">
          <FunctionOutlined />
          <span className="category-title">函数计算</span>
        </div>
      ),
      children: (
        <div className="component-list">
          <div
            className="component-item"
            onClick={() => handleAddNode({
              type: 'function',
              nodeType: 'function',
              label: '函数计算',
              description: '多输入计算',
              icon: <FunctionOutlined />,
              color: '#722ed1',
            })}
            style={{ borderColor: '#722ed1' }}
          >
            <div className="component-item-inner">
              <span className="component-icon" style={{ color: '#722ed1' }}>
                <FunctionOutlined />
              </span>
              <div className="component-content">
                <div className="component-label">函数计算</div>
                <div className="component-description">多输入计算</div>
              </div>
              <PlusOutlined className="component-add" />
            </div>
          </div>
        </div>
      ),
    },
    {
      key: 'roi',
      label: (
        <div className="collapse-item-label">
          <EditOutlined />
          <span className="category-title">图像处理</span>
        </div>
      ),
      children: (
        <div className="component-list">
          <div
            className="component-item"
            onClick={() => handleAddNode({
              type: 'roi',
              nodeType: 'roi',
              label: '热区绘制',
              description: '绘制ROI区域',
              icon: <EditOutlined />,
              color: '#fa8c16',
            })}
            style={{ borderColor: '#fa8c16' }}
          >
            <div className="component-item-inner">
              <span className="component-icon" style={{ color: '#fa8c16' }}>
                <EditOutlined />
              </span>
              <div className="component-content">
                <div className="component-label">热区绘制</div>
                <div className="component-description">绘制ROI区域</div>
              </div>
              <PlusOutlined className="component-add" />
            </div>
          </div>
        </div>
      ),
    },
    {
      key: 'output',
      label: (
        <div className="collapse-item-label">
          <AppstoreOutlined />
          <span className="category-title">输出</span>
        </div>
      ),
      children: (
        <div className="component-list">
          <div
            className="component-item"
            onClick={() => handleAddNode({
              type: 'alert',
              nodeType: 'alert',
              label: '告警输出',
              description: '发送告警',
              icon: <BellOutlined />,
              color: '#f5222d',
              alertLevel: 'info',
              alertMessage: '检测到目标',
              alertType: 'detection',
              messageFormat: 'detailed',  // 添加默认消息格式
              vlValidation: {
                enable: false,
                promptTemplate: '',
              },
            })}
            style={{ borderColor: '#f5222d' }}
          >
            <div className="component-item-inner">
              <span className="component-icon" style={{ color: '#f5222d' }}>
                <BellOutlined />
              </span>
              <div className="component-content">
                <div className="component-label">告警输出</div>
                <div className="component-description">发送告警</div>
              </div>
              <PlusOutlined className="component-add" />
            </div>
          </div>
          <div
            className="component-item"
            onClick={() => handleAddNode({
              type: 'webhook',
              nodeType: 'webhook',
              label: 'Webhook 推送',
              description: '推送真实告警事件',
              icon: <SendOutlined />,
              color: '#13c2c2',
              config: {
                provider: 'generic',
                endpoint_url: '',
                headers: [],
                title_template: '【{{alert.level}}】{{alert.type}}',
                body_template: '{{alert.message}}',
                payload_template: {},
                include_media_urls: true,
                public_base_url: '',
                timeout_seconds: 5,
                max_attempts: 3,
                retry_backoff_seconds: 1,
                provider_options: {},
              },
            })}
            style={{ borderColor: '#13c2c2' }}
          >
            <div className="component-item-inner">
              <span className="component-icon" style={{ color: '#08979c' }}>
                <SendOutlined />
              </span>
              <div className="component-content">
                <div className="component-label">Webhook 推送</div>
                <div className="component-description">连接告警输出，异步推送事件</div>
              </div>
              <PlusOutlined className="component-add" />
            </div>
          </div>
        </div>
      ),
    },
  ];

  return (
    <div className="component-sidebar">
      <div className="sidebar-header">
        <AppstoreOutlined className="sidebar-icon" />
        <span className="sidebar-title">组件库</span>
      </div>

      <Collapse
        defaultActiveKey={['videoSource', 'algorithm', 'function', 'output']}
        items={items}
        className="component-collapse"
        ghost
      />
    </div>
  );
};

export default ComponentSidebar;
