import { tr, trf } from '@/i18n/tr';
import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { BellOutlined } from '@ant-design/icons';
import './BaseNode.css';

const AlertNode = ({ data }: any) => {
  // 获取 suppression 配置的显示文本
  const getSuppressionText = () => {
    if (!data.suppression) return tr("未配置");

    const { mode, simple_seconds, window_size, window_mode, window_threshold } = data.suppression;

    if (mode === 'simple') {
      return trf("__VAR0__秒抑制", [simple_seconds || 60]);
    } else if (mode === 'window') {
      let modeText = '';

      if (window_mode === 'ratio') {
        modeText = `${(window_threshold * 100).toFixed(0)}%`;
      } else if (window_mode === 'count') {
        modeText = trf("≥__VAR0__次", [window_threshold]);
      } else if (window_mode === 'consecutive') {
        modeText = trf("连续__VAR0__次", [window_threshold]);
      }

      return trf("窗口__VAR0__s/__VAR1__", [window_size, modeText]);
    }

    return tr("未配置");
  };

  return (
    <div className="custom-node alert-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle" />
      <Handle type="source" position={Position.Right} id="output" className="node-handle" />
      <div className="node-header">
        <BellOutlined className="node-icon" />
        <span className="node-title">{data.label}</span>
      </div>
      {data.description && (
        <div className="node-description">{data.description}</div>
      )}
      {data.alertLevel && (
        <div className="node-meta">
          <span className="meta-label">{tr("级别:")}</span>
          <span className={`meta-value alert-level-${data.alertLevel}`}>
            {data.alertLevel === 'info' && tr("信息")}
            {data.alertLevel === 'warning' && tr("警告")}
            {data.alertLevel === 'error' && tr("错误")}
            {data.alertLevel === 'critical' && tr("严重")}
          </span>
        </div>
      )}
      {data.alertType && (
        <div className="node-meta">
          <span className="meta-label">{tr("类型:")}</span>
          <span className="meta-value">{data.alertType}</span>
        </div>
      )}
      {data.suppression && (
        <div className="node-meta">
          <span className="meta-label">{tr("抑制:")}</span>
          <span className="meta-value">{getSuppressionText()}</span>
        </div>
      )}
      {data.vlValidation?.enable && (
        <div className="node-meta">
          <span className="meta-label">VL:</span>
          <span className="meta-value">{tr("已启用")}</span>
        </div>
      )}
    </div>
  );
};

export default memo(AlertNode);
