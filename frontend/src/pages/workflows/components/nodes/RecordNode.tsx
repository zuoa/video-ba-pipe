import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { RecordingOutlined } from '@ant-design/icons';
import './BaseNode.css';

const RecordNode = ({ data }: any) => {
  return (
    <div className="custom-node record-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle" />
      <div className="node-header">
        <RecordingOutlined className="node-icon" />
        <span className="node-title">{data.label}</span>
      </div>
      {data.description && (
        <div className="node-description">{data.description}</div>
      )}
      {data.recordDuration && (
        <div className="node-meta">
          <span className="meta-label">时长:</span>
          <span className="meta-value">{data.recordDuration}秒</span>
        </div>
      )}
    </div>
  );
};

export default memo(RecordNode);
