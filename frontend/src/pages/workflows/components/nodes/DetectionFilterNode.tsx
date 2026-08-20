import React, { memo } from 'react';
import { ColumnHeightOutlined } from '@ant-design/icons';
import { Handle, Position } from 'reactflow';
import './BaseNode.css';

const DIMENSION_LABELS: Record<string, string> = {
  height: '高度',
  width: '宽度',
};

const DetectionFilterNode = ({ data }: any) => {
  const config = data.config || {};
  const dimension = DIMENSION_LABELS[config.dimension] || '高度';
  const comparison = config.comparison === 'lte' ? '≤' : '≥';
  const threshold = config.unit === 'ratio'
    ? `${Number(config.threshold || 0) * 100}%`
    : `${config.threshold ?? 0}px`;

  return (
    <div className="custom-node detection-filter-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle detection-filter-handle" />
      <Handle type="source" position={Position.Right} id="output" className="node-handle detection-filter-handle" />
      <div className="node-header">
        <ColumnHeightOutlined className="node-icon" />
        <span className="node-title">{data.label}</span>
      </div>
      {data.description ? <div className="node-description">{data.description}</div> : null}
      <div className="node-condition detection-filter-rule">
        {dimension} {comparison} {threshold}
      </div>
    </div>
  );
};

export default memo(DetectionFilterNode);
