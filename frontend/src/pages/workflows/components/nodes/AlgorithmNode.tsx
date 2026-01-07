import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { BugOutlined } from '@ant-design/icons';
import './BaseNode.css';

const AlgorithmNode = ({ data }: any) => {
  return (
    <div className="custom-node algorithm-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle" />
      <Handle type="source" position={Position.Right} id="output" className="node-handle" />
      <div className="node-header">
        <BugOutlined className="node-icon" />
        <span className="node-title">{data.label}</span>
      </div>
      {data.description && (
        <div className="node-description">{data.description}</div>
      )}
      {data.confidence && (
        <div className="node-meta">
          <span className="meta-label">置信度:</span>
          <span className="meta-value">{(data.confidence * 100).toFixed(0)}%</span>
        </div>
      )}
    </div>
  );
};

export default memo(AlgorithmNode);
