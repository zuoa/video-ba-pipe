import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { BellOutlined } from '@ant-design/icons';
import './BaseNode.css';

const AlertNode = ({ data }: any) => {
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
    </div>
  );
};

export default memo(AlertNode);
