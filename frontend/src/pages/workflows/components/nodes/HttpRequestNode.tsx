import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { GlobalOutlined } from '@ant-design/icons';
import './BaseNode.css';

const HttpRequestNode = ({ data }: any) => {
  const config = data.config || {};
  const method = config.method || 'GET';
  const outputCount = Array.isArray(config.extractors) ? config.extractors.length : 0;
  return (
    <div className="custom-node algorithm-node http-request-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle" />
      <Handle type="source" position={Position.Right} id="output" className="node-handle" />
      <div className="node-header">
        <GlobalOutlined className="node-icon" />
        <span className="node-title">{data.label || 'HTTP 请求'}</span>
      </div>
      <div className="node-description">{config.url || '尚未配置 URL'}</div>
      <div className="node-meta">
        <span className="meta-label">请求:</span>
        <span className="meta-value">{method} → response</span>
      </div>
      <div className="node-meta">
        <span className="meta-label">输出:</span>
        <span className="meta-value">{outputCount} 个命名变量</span>
      </div>
    </div>
  );
};

export default memo(HttpRequestNode);
