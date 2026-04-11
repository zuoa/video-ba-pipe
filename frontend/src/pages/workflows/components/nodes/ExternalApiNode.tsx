import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { ApiOutlined } from '@ant-design/icons';
import './BaseNode.css';

const ExternalApiNode = ({ data }: any) => {
  const executionMode = data.executionMode || 'sync';

  return (
    <div className="custom-node algorithm-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle" />
      <Handle type="source" position={Position.Right} id="output" className="node-handle" />
      <div className="node-header">
        <ApiOutlined className="node-icon" />
        <span className="node-title">{data.label}</span>
      </div>
      {data.description && (
        <div className="node-description">{data.description}</div>
      )}
      <div className="node-meta">
        <span className="meta-label">模式:</span>
        <span className="meta-value">{executionMode === 'async_submit' ? '异步提交' : '同步等待'}</span>
      </div>
      {data.externalApiName && (
        <div className="node-meta">
          <span className="meta-label">API:</span>
          <span className="meta-value">{data.externalApiName}</span>
        </div>
      )}
    </div>
  );
};

export default memo(ExternalApiNode);
