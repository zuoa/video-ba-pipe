import React from 'react';
import { Input, Select, Checkbox, Space } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import './FilterBar.css';

interface ModelFilter {
  search?: string;
  type?: string;
  framework?: string;
  enabledOnly?: boolean;
}

interface FilterBarProps {
  modelTypes: string[];
  modelFrameworks: string[];
  filter: ModelFilter;
  onFilterChange: (filter: Partial<ModelFilter>) => void;
}

const FilterBar: React.FC<FilterBarProps> = ({
  modelTypes,
  modelFrameworks,
  filter,
  onFilterChange,
}) => {
  return (
    <div className="filter-bar">
      <Space size="middle" wrap>
        <Input
          className="filter-search"
          placeholder="搜索模型名称或描述..."
          prefix={<SearchOutlined />}
          value={filter.search || ''}
          onChange={(e) => onFilterChange({ search: e.target.value || undefined })}
          allowClear
        />
        <Select
          className="filter-select"
          placeholder="所有类型"
          value={filter.type}
          onChange={(value) => onFilterChange({ type: value || undefined })}
          allowClear
        >
          {modelTypes.map((type) => (
            <Select.Option key={type} value={type}>
              {type}
            </Select.Option>
          ))}
        </Select>
        <Select
          className="filter-select"
          placeholder="所有框架"
          value={filter.framework}
          onChange={(value) => onFilterChange({ framework: value || undefined })}
          allowClear
        >
          {modelFrameworks.map((fw) => (
            <Select.Option key={fw} value={fw}>
              {fw}
            </Select.Option>
          ))}
        </Select>
        <Checkbox
          checked={filter.enabledOnly}
          onChange={(e) => onFilterChange({ enabledOnly: e.target.checked || undefined })}
        >
          仅显示启用
        </Checkbox>
      </Space>
    </div>
  );
};

export default FilterBar;
