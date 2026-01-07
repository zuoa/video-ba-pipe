import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { BranchesOutlined } from '@ant-design/icons';
import './BaseNode.css';

const ConditionNode = ({ data }: any) => {
  return (
    <div className="custom-node condition-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle" />
      <Handle type="source" position={Position.Right} id="yes" className="node-handle node-handle-yes" style={{ top: '30%' }} />
      <Handle type="source" position={Position.Right} id="no" className="node-handle node-handle-no" style={{ top: '70%' }} />
      <div className="node-header">
        <BranchesOutlined className="node-icon" />
        <span className="node-title">{data.label}</span>
      </div>
      {data.description && (
        <div className="node-description">{data.description}</div>
      )}
      <div className="node-branches">
        <div className="branch yes">
          <span className="branch-label">是</span>
        </div>
        <div className="branch no">
          <span className="branch-label">否</span>
        </div>
      </div>
    </div>
  );
};

export default memo(ConditionNode);
