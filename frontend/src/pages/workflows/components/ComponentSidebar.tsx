import React, { useState, useEffect } from 'react';
import { Collapse } from 'antd';
import {
  VideoCameraOutlined,
  BugOutlined,
  BranchesOutlined,
  EditOutlined,
  BellOutlined,
  PlusOutlined,
  AppstoreOutlined,
  FunctionOutlined,
} from '@ant-design/icons';
import { getAlgorithms } from '@/services/api';
import './ComponentSidebar.css';

export interface ComponentSidebarProps {
  onAddNode: (nodeData: any) => void;
  videoSources: any[];
}

const ComponentSidebar: React.FC<ComponentSidebarProps> = ({ onAddNode, videoSources }) => {
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
            onClick={() => handleAddNode({
              type: 'videoSource',
              nodeType: 'videoSource',
              label: '视频源',
              description: '选择视频源',
              icon: <VideoCameraOutlined />,
              color: '#1890ff',
            })}
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
          {algorithms.length > 0 ? (
            algorithms.map((algo, index) => (
              <div
                key={index}
                className="component-item"
                onClick={() => handleAddNode({
                  type: 'algorithm',
                  nodeType: 'algorithm',
                  label: algo.name,
                  description: algo.description || '算法检测',
                  icon: <BugOutlined />,
                  color: '#52c41a',
                  algorithmId: algo.id,
                  dataId: algo.id,
                  // 从算法的 ext_config_json 中读取执行配置作为默认值
                  config: {
                    interval_seconds: algo.interval_seconds || 1,
                    runtime_timeout: algo.runtime_timeout || 30,
                    memory_limit_mb: algo.memory_limit_mb || 512,
                    label_name: algo.label_name || 'Object',
                    label_color: algo.label_color || '#FF0000',
                    window_detection: {
                      enable: algo.enable_window_check || false,
                      window_size: algo.window_size || 30,
                      window_mode: algo.window_mode || 'ratio',
                      window_threshold: algo.window_threshold !== undefined ? algo.window_threshold : 0.3,
                    },
                  },
                })}
                style={{ borderColor: '#52c41a' }}
              >
                <div className="component-item-inner">
                  <span className="component-icon" style={{ color: '#52c41a' }}>
                    <BugOutlined />
                  </span>
                  <div className="component-content">
                    <div className="component-label">{algo.name}</div>
                    <div className="component-description">{algo.description || '算法检测'}</div>
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
