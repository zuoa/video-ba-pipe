import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { BellOutlined } from '@ant-design/icons';
import './BaseNode.css';

const AlertNode = ({ data }: any) => {
  // 获取 suppression 配置的显示文本
  const getSuppressionText = () => {
    if (!data.suppression) return '未配置';

    const { mode, simple_seconds, window_size, window_mode, window_threshold } = data.suppression;

    if (mode === 'simple') {
      return `${simple_seconds || 60}秒抑制`;
    } else if (mode === 'window') {
      let modeText = '';

      if (window_mode === 'ratio') {
        modeText = `${(window_threshold * 100).toFixed(0)}%`;
      } else if (window_mode === 'count') {
        modeText = `≥${window_threshold}次`;
      } else if (window_mode === 'consecutive') {
        modeText = `连续${window_threshold}次`;
      }

      return `窗口${window_size}s/${modeText}`;
    }

    return '未配置';
  };

  return (
    <div className="custom-node alert-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle" />
      <div className="node-header">
        <BellOutlined className="node-icon" />
        <span className="node-title">{data.label}</span>
      </div>
      {data.description && (
        <div className="node-description">{data.description}</div>
      )}
      {data.alertLevel && (
        <div className="node-meta">
          <span className="meta-label">级别:</span>
          <span className={`meta-value alert-level-${data.alertLevel}`}>
            {data.alertLevel === 'info' && '信息'}
            {data.alertLevel === 'warning' && '警告'}
            {data.alertLevel === 'error' && '错误'}
            {data.alertLevel === 'critical' && '严重'}
          </span>
        </div>
      )}
      {data.alertType && (
        <div className="node-meta">
          <span className="meta-label">类型:</span>
          <span className="meta-value">{data.alertType}</span>
        </div>
      )}
      {data.suppression && (
        <div className="node-meta">
          <span className="meta-label">抑制:</span>
          <span className="meta-value">{getSuppressionText()}</span>
        </div>
      )}
    </div>
  );
};

export default memo(AlertNode);
