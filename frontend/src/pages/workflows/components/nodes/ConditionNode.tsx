import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { BranchesOutlined } from '@ant-design/icons';
import './BaseNode.css';

const ConditionNode = ({ data }: any) => {
  // 获取条件配置用于显示
  const comparisonType = data.comparisonType || data.comparison_type || '>=';
  const targetCount = data.targetCount || data.target_count || 1;
  const conditionKind = data.conditionKind || data.condition_kind || 'count';

  // 生成条件描述
  const getConditionLabel = () => {
    if (conditionKind === 'http_value') {
      const expression = data.expression;
      const countRules = (group: any): number => Array.isArray(group?.children)
        ? group.children.reduce((total: number, child: any) => total + ('children' in child ? countRules(child) : 1), 0)
        : 0;
      return `API 值 · ${countRules(expression)} 条规则`;
    }
    if (conditionKind === 'ocr_text') {
      const operator = (data.textOperator || data.text_operator) === 'not_contains' ? '不包含' : '包含';
      const patternType = data.patternType || data.pattern_type || 'keywords';
      const value = patternType === 'regex'
        ? (data.regexPattern || data.regex_pattern || '未配置')
        : (data.keywords || []).join(' / ') || '未配置';
      return `${operator} ${value}`;
    }
    if (conditionKind === 'count_change') {
      const direction = data.direction === 'increase'
        ? '骤增'
        : data.direction === 'decrease'
          ? '骤减'
          : '骤增/骤减';
      const windowSize = data.windowSize ?? data.window_size ?? 10;
      const relativeThreshold = data.relativeThreshold ?? data.relative_threshold ?? 0.5;
      const absoluteThreshold = data.absoluteThreshold ?? data.absolute_threshold ?? 3;
      return `${direction} · ${windowSize}次 · ≥${Math.round(relativeThreshold * 100)}% 且 ≥${absoluteThreshold}个`;
    }
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
