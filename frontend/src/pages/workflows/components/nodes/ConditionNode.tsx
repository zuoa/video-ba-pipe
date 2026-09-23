import { tr, trf } from '@/i18n/tr';
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
      return trf("API 值 · __VAR0__ 条规则", [countRules(expression)]);
    }
    if (conditionKind === 'ocr_text') {
      const operator = (data.textOperator || data.text_operator) === 'not_contains' ? tr("不包含") : tr("包含");
      const patternType = data.patternType || data.pattern_type || 'keywords';
      const value = patternType === 'regex'
        ? (data.regexPattern || data.regex_pattern || tr("未配置"))
        : (data.keywords || []).join(' / ') || tr("未配置");
      return `${operator} ${value}`;
    }
    if (conditionKind === 'count_change') {
      const direction = data.direction === 'increase'
        ? tr("骤增")
        : data.direction === 'decrease'
          ? tr("骤减")
          : tr("骤增/骤减");
      const windowSize = data.windowSize ?? data.window_size ?? 10;
      const relativeThreshold = data.relativeThreshold ?? data.relative_threshold ?? 0.5;
      const absoluteThreshold = data.absoluteThreshold ?? data.absolute_threshold ?? 3;
      return trf("__VAR0__ · __VAR1__次 · ≥__VAR2__% 且 ≥__VAR3__个", [direction, windowSize, Math.round(relativeThreshold * 100), absoluteThreshold]);
    }
    if (comparisonType === '>=') {
      return trf("数量 ≥ __VAR0__", [targetCount]);
    } else if (comparisonType === '==') {
      return trf("数量 = __VAR0__", [targetCount]);
    }
    return tr("条件判断");
  };

  return (
    <div className="custom-node condition-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle" />
      <Handle type="source" position={Position.Right} id="yes" className="node-handle node-handle-yes" style={{ top: '30%' }} />
      <Handle type="source" position={Position.Right} id="no" className="node-handle node-handle-no" style={{ top: '70%' }} />
      <div className="node-header">
        <BranchesOutlined className="node-icon" />
        <span className="node-title">{data.label || tr("检测条件")}</span>
      </div>
      <div className="node-condition">{getConditionLabel()}</div>
      {data.description && (
        <div className="node-description">{data.description}</div>
      )}
      <div className="node-branches">
        <div className="branch yes">
          <span className="branch-label">{tr("满足")}</span>
        </div>
        <div className="branch no">
          <span className="branch-label">{tr("不满足")}</span>
        </div>
      </div>
    </div>
  );
};

export default memo(ConditionNode);
