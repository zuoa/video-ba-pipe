import React, { memo, useCallback } from 'react';
import { Input, Select, Space } from 'antd';
import Button from '@/components/common/AppButton';
import { DeleteOutlined, PlusOutlined, ApartmentOutlined } from '@ant-design/icons';
import './HttpConditionBuilder.css';

export type HttpConditionRule = {
  variable: string;
  operator: string;
  value?: unknown;
};

export type HttpConditionGroup = {
  logic: 'and' | 'or';
  children: Array<HttpConditionGroup | HttpConditionRule>;
};

type Props = {
  value?: HttpConditionGroup;
  onChange?: (value: HttpConditionGroup) => void;
  variables?: string[];
};

const OPERATORS = [
  ['eq', '等于'], ['ne', '不等于'], ['gt', '大于'], ['gte', '大于等于'],
  ['lt', '小于'], ['lte', '小于等于'], ['contains', '包含'],
  ['not_contains', '不包含'], ['in', '属于'], ['not_in', '不属于'],
  ['exists', '存在'], ['not_exists', '不存在'], ['truthy', '为真'], ['falsy', '为假'],
] as const;

const VALUELESS = new Set(['exists', 'not_exists', 'truthy', 'falsy']);

const defaultRule = (variables: string[]): HttpConditionRule => ({
  variable: variables[0] || '$success',
  operator: 'eq',
  value: true,
});

function parseComparisonValue(raw: string): unknown {
  const trimmed = raw.trim();
  if (!trimmed) return '';
  try {
    return JSON.parse(trimmed);
  } catch {
    return raw;
  }
}

function displayComparisonValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === undefined) return '';
  return JSON.stringify(value);
}

type GroupProps = {
  group: HttpConditionGroup;
  variables: string[];
  depth: number;
  onChange: (group: HttpConditionGroup) => void;
};

const ConditionGroup = memo(({ group, variables, depth, onChange }: GroupProps) => {
  const updateChild = useCallback((index: number, child: HttpConditionGroup | HttpConditionRule) => {
    const children = group.children.map((item, itemIndex) => itemIndex === index ? child : item);
    onChange({ ...group, children });
  }, [group, onChange]);

  const removeChild = useCallback((index: number) => {
    onChange({ ...group, children: group.children.filter((_, itemIndex) => itemIndex !== index) });
  }, [group, onChange]);

  return (
    <div className={`http-condition-group depth-${Math.min(depth, 3)}`}>
      <div className="http-condition-group-head">
        <span className="http-condition-rail">{depth === 0 ? '满足' : '条件组'}</span>
        <Select
          size="small"
          value={group.logic}
          style={{ width: 92 }}
          options={[{ label: '全部 AND', value: 'and' }, { label: '任一 OR', value: 'or' }]}
          onChange={(logic) => onChange({ ...group, logic })}
        />
      </div>

      <div className="http-condition-children">
        {group.children.map((child, index) => {
          const nested = 'children' in child;
          return (
            <div className="http-condition-child" key={`${depth}-${index}`}>
              {nested ? (
                <ConditionGroup
                  group={child}
                  variables={variables}
                  depth={depth + 1}
                  onChange={(next) => updateChild(index, next)}
                />
              ) : (
                <div className="http-condition-rule">
                  <Select
                    value={child.variable}
                    options={variables.map((variable) => ({ label: variable, value: variable }))}
                    onChange={(variable) => updateChild(index, { ...child, variable })}
                    style={{ minWidth: 130, flex: 1 }}
                  />
                  <Select
                    value={child.operator}
                    options={OPERATORS.map(([value, label]) => ({ value, label }))}
                    onChange={(operator) => updateChild(index, { ...child, operator })}
                    style={{ width: 108 }}
                  />
                  {VALUELESS.has(child.operator) ? null : (
                    <Input
                      value={displayComparisonValue(child.value)}
                      onChange={(event) => updateChild(index, {
                        ...child,
                        value: parseComparisonValue(event.target.value),
                      })}
                      placeholder='比较值，如 0.8、true、["a"]'
                      style={{ minWidth: 150, flex: 1 }}
                    />
                  )}
                </div>
              )}
              <Button
                type="text"
                danger
                size="small"
                aria-label="删除条件"
                icon={<DeleteOutlined />}
                onClick={() => removeChild(index)}
              />
            </div>
          );
        })}
        {group.children.length === 0 ? (
          <div className="http-condition-empty">添加一条规则后才能保存条件。</div>
        ) : null}
      </div>

      <Space size={8} className="http-condition-actions">
        <Button
          size="small"
          icon={<PlusOutlined />}
          onClick={() => onChange({ ...group, children: [...group.children, defaultRule(variables)] })}
        >
          添加规则
        </Button>
        {depth < 4 ? (
          <Button
            size="small"
            icon={<ApartmentOutlined />}
            onClick={() => onChange({
              ...group,
              children: [...group.children, { logic: 'and', children: [defaultRule(variables)] }],
            })}
          >
            添加条件组
          </Button>
        ) : null}
      </Space>
    </div>
  );
});

const HttpConditionBuilder = ({ value, onChange, variables = [] }: Props) => {
  const allVariables = ['$success', '$status_code', '$error_type', ...variables];
  const group = value && Array.isArray(value.children)
    ? value
    : { logic: 'and' as const, children: [defaultRule(allVariables)] };
  return (
    <ConditionGroup
      group={group}
      variables={allVariables}
      depth={0}
      onChange={(next) => onChange?.(next)}
    />
  );
};

export default memo(HttpConditionBuilder);
