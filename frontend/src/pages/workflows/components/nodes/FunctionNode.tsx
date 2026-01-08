import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { FunctionOutlined } from '@ant-design/icons';
import './BaseNode.css';

const FunctionNode = ({ data }: any) => {
  return (
    <div className="custom-node function-node">
      <Handle type="target" position={Position.Left} id="input-a" className="node-handle" style={{ top: '30%' }} />
      <Handle type="target" position={Position.Left} id="input-b" className="node-handle" style={{ top: '70%' }} />
      <Handle type="source" position={Position.Right} id="output" className="node-handle" />
      <div className="node-header">
        <FunctionOutlined className="node-icon" />
        <span className="node-title">{data.label}</span>
      </div>
      {data.description && (
        <div className="node-description">{data.description}</div>
      )}
      {data.functionName && (
        <div className="node-meta">
          <span className="meta-label">函数:</span>
          <span className="meta-value">{data.functionName}</span>
        </div>
      )}
      {data.threshold !== undefined && (
        <div className="node-meta">
          <span className="meta-label">阈值:</span>
          <span className="meta-value">{data.threshold}</span>
        </div>
      )}
    </div>
  );
};

export default memo(FunctionNode);

