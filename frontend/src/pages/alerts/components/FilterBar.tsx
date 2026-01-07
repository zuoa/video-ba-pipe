import React from 'react';
import { Select, Button, Space, Tag, Card } from 'antd';
import {
  SyncOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons';
import { Task } from '../types';

interface FilterBarProps {
  tasks: Task[];
  alertTypes: string[];
  selectedTask?: string;
  selectedAlertType?: string;
  onTaskChange: (value: string) => void;
  onAlertTypeChange: (value: string) => void;
  onRefresh: () => void;
  loading?: boolean;
}

const FilterBar: React.FC<FilterBarProps> = ({
  tasks,
  alertTypes,
  selectedTask,
  selectedAlertType,
  onTaskChange,
  onAlertTypeChange,
  onRefresh,
  loading = false,
}) => {
  return (
    <Card style={{ marginBottom: 16 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Space size="middle" wrap>
          <Space.Compact>
            <Select
              placeholder="选择任务"
              value={selectedTask}
              onChange={onTaskChange}
              allowClear
              style={{ width: 200 }}
              options={tasks.map(task => ({
                label: task.name,
                value: task.id.toString(),
              }))}
            />
            <Select
              placeholder="告警类型"
              value={selectedAlertType}
              onChange={onAlertTypeChange}
              allowClear
              style={{ width: 200 }}
              options={alertTypes.map(type => ({
                label: type,
                value: type,
              }))}
            />
          </Space.Compact>

          {(selectedTask || selectedAlertType) && (
            <Space>
              {selectedTask && (
                <Tag
                  closable
                  onClose={() => onTaskChange('')}
                  closeIcon={<CloseCircleOutlined />}
                >
                  {tasks.find(t => t.id === parseInt(selectedTask))?.name}
                </Tag>
              )}
              {selectedAlertType && (
                <Tag
                  closable
                  onClose={() => onAlertTypeChange('')}
                  closeIcon={<CloseCircleOutlined />}
                >
                  {selectedAlertType}
                </Tag>
              )}
            </Space>
          )}
        </Space>

        <Button
          type="primary"
          icon={<SyncOutlined spin={loading} />}
          onClick={onRefresh}
          loading={loading}
        >
          刷新
        </Button>
      </Space>
    </Card>
  );
};

export default FilterBar;
