import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { ClockCircleOutlined } from '@ant-design/icons';
import { summarizeWeeklySchedule } from '../../utils/timeSchedule';
import './BaseNode.css';

const TimeScheduleNode = ({ data }: any) => (
  <div className="custom-node time-schedule-node">
    <Handle type="target" position={Position.Left} id="input" className="node-handle" />
    <Handle type="source" position={Position.Right} id="output" className="node-handle time-schedule-handle" />
    <div className="node-header">
      <ClockCircleOutlined className="node-icon" />
      <span className="node-title">{data.label || '时间启用区间'}</span>
    </div>
    <div className="time-schedule-node-summary">
      {summarizeWeeklySchedule(data.weeklySchedule)}
    </div>
    <div className="node-description">命中时段才继续</div>
  </div>
);

export default memo(TimeScheduleNode);
