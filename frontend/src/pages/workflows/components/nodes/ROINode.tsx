import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { BorderOutlined } from '@ant-design/icons';
import './BaseNode.css';

const ROINode = ({ data }: any) => {
  return (
    <div className="custom-node roi-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle" />
      <Handle type="source" position={Position.Right} id="output" className="node-handle" />
      <div className="node-header">
        <BorderOutlined className="node-icon" />
        <span className="node-title">{data.label}</span>
      </div>
      {data.description && (
        <div className="node-description">{data.description}</div>
      )}
      {data.roiMode && (
        <div className="node-meta">
          <span className="meta-label">模式:</span>
          <span className="meta-value">{data.roiMode === 'preMask' ? '前置掩码' : '后置过滤'}</span>
        </div>
      )}
    </div>
  );
};

export default memo(ROINode);
