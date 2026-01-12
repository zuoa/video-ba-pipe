import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { BranchesOutlined } from '@ant-design/icons';
import './BaseNode.css';

const ConditionNode = ({ data }: any) => {
  // 获取条件配置用于显示
  const comparisonType = data.comparisonType || data.comparison_type || '>=';
  const targetCount = data.targetCount || data.target_count || 1;

  // 生成条件描述
  const getConditionLabel = () => {
    if (comparisonType === '>=') {
      return `数量 ≥ ${targetCount}`;
    } else if (comparisonType === '==') {
      return `数量 = ${targetCount}`;
    }
    return `条件判断`;
  };

  return (
    <div className="custom-node condition-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle" />
      <Handle type="source" position={Position.Right} id="yes" className="node-handle node-handle-yes" style={{ top: '30%' }} />
      <Handle type="source" position={Position.Right} id="no" className="node-handle node-handle-no" style={{ top: '70%' }} />
      <div className="node-header">
        <BranchesOutlined className="node-icon" />
        <span className="node-title">{data.label || '检测条件'}</span>
      </div>
      <div className="node-condition">{getConditionLabel()}</div>
      {data.description && (
        <div className="node-description">{data.description}</div>
      )}
      <div className="node-branches">
        <div className="branch yes">
          <span className="branch-label">满足</span>
        </div>
        <div className="branch no">
          <span className="branch-label">不满足</span>
        </div>
      </div>
    </div>
  );
};

export default memo(ConditionNode);
