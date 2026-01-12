import React, { useState } from 'react';
import { Select, Button, Space, Tag, Card, DatePicker } from 'antd';
import {
  SyncOutlined,
  CloseCircleOutlined,
  CalendarOutlined,
  ApartmentOutlined,
  VideoCameraOutlined,
  BugOutlined,
} from '@ant-design/icons';
import dayjs, { Dayjs } from 'dayjs';
import { Task, Workflow } from '../types';

const { RangePicker } = DatePicker;

interface FilterBarProps {
  tasks: Task[];
  workflows: Workflow[];
  alertTypes: string[];
  selectedTask?: string;
  selectedWorkflow?: string;
  selectedAlertType?: string;
  selectedTimeRange?: string;
  customTimeRange?: { start: string; end: string };
  onTaskChange: (value: string) => void;
  onWorkflowChange: (value: string) => void;
  onAlertTypeChange: (value: string) => void;
  onTimeRangeChange: (value: string, customRange?: { start: string; end: string }) => void;
  onRefresh: () => void;
  loading?: boolean;
}

const FilterBar: React.FC<FilterBarProps> = ({
  tasks,
  workflows,
  alertTypes,
  selectedTask,
  selectedWorkflow,
  selectedAlertType,
  selectedTimeRange,
  customTimeRange,
  onTaskChange,
  onWorkflowChange,
  onAlertTypeChange,
  onTimeRangeChange,
  onRefresh,
  loading = false,
}) => {
  // 自定义时间范围的状态
  const [customDateRange, setCustomDateRange] = useState<[Dayjs, Dayjs] | null>(null);

  // 时间范围选项
  const timeRangeOptions = [
    { label: '最近1小时', value: '1h' },
    { label: '最近24小时', value: '24h' },
    { label: '最近7天', value: '7d' },
    { label: '最近30天', value: '30d' },
    { label: '自定义', value: 'custom' },
  ];

  // 处理自定义时间范围
  const handleDateRangeChange = (dates: [Dayjs, Dayjs] | null) => {
    setCustomDateRange(dates);
    if (dates && dates[0] && dates[1]) {
      // 将日期转换为 ISO 格式并传递给父组件
      onTimeRangeChange('custom', {
        start: dates[0].toISOString(),
        end: dates[1].toISOString(),
      });
    } else {
      onTimeRangeChange('');
    }
  };

  // 获取显示标签
  const getTaskLabel = () => {
    if (!selectedTask) return null;
    const task = tasks.find(t => t.id === parseInt(selectedTask));
    return task?.name || selectedTask;
  };

  const getWorkflowLabel = () => {
    if (!selectedWorkflow) return null;
    const workflow = workflows.find(w => w.id === parseInt(selectedWorkflow));
    return workflow?.name || selectedWorkflow;
  };

  const getTimeRangeLabel = () => {
    if (!selectedTimeRange) return null;
    const option = timeRangeOptions.find(o => o.value === selectedTimeRange);
    return option?.label || selectedTimeRange;
  };

  return (
    <Card style={{ marginBottom: 16 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
        <Space size="middle" wrap>
          {/* 视频源筛选 */}
          <Select
            placeholder={<span><VideoCameraOutlined /> 选择视频源</span>}
            value={selectedTask}
            onChange={onTaskChange}
            allowClear
            style={{ width: 180 }}
            options={tasks.map(task => ({
              label: task.name,
              value: task.id.toString(),
            }))}
          />

          {/* 流程编排筛选 */}
          <Select
            placeholder={<span><ApartmentOutlined /> 选择流程编排</span>}
            value={selectedWorkflow}
            onChange={onWorkflowChange}
            allowClear
            style={{ width: 180 }}
            options={workflows.map(workflow => ({
              label: workflow.name,
              value: workflow.id.toString(),
            }))}
          />

          {/* 告警类型筛选 */}
          <Select
            placeholder={<span><BugOutlined /> 告警类型</span>}
            value={selectedAlertType}
            onChange={onAlertTypeChange}
            allowClear
            style={{ width: 160 }}
            options={alertTypes.map(type => ({
              label: type,
              value: type,
            }))}
          />

          {/* 时间范围筛选 */}
          <Select
            placeholder={<span><CalendarOutlined /> 时间范围</span>}
            value={selectedTimeRange}
            onChange={(value) => {
              // 切换到非自定义选项时，清除自定义时间范围
              if (value !== 'custom') {
                setCustomDateRange(null);
              }
              onTimeRangeChange(value || '');
            }}
            allowClear
            style={{ width: 140 }}
            options={timeRangeOptions}
          />

          {/* 自定义日期选择器（当选择自定义时间范围时显示） */}
          {selectedTimeRange === 'custom' && (
            <RangePicker
              showTime
              format="YYYY-MM-DD HH:mm:ss"
              value={customDateRange}
              onChange={handleDateRangeChange}
              style={{ width: 380 }}
            />
          )}

          {/* 活跃筛选条件标签 */}
          {(selectedTask || selectedWorkflow || selectedAlertType || selectedTimeRange) && (
            <Space wrap>
              {selectedTask && (
                <Tag
                  closable
                  onClose={() => onTaskChange('')}
                  closeIcon={<CloseCircleOutlined />}
                  color="blue"
                >
                  视频源: {getTaskLabel()}
                </Tag>
              )}
              {selectedWorkflow && (
                <Tag
                  closable
                  onClose={() => onWorkflowChange('')}
                  closeIcon={<CloseCircleOutlined />}
                  color="purple"
                >
                  流程编排: {getWorkflowLabel()}
                </Tag>
              )}
              {selectedAlertType && (
                <Tag
                  closable
                  onClose={() => onAlertTypeChange('')}
                  closeIcon={<CloseCircleOutlined />}
                  color="orange"
                >
                  类型: {selectedAlertType}
                </Tag>
              )}
              {selectedTimeRange && selectedTimeRange !== 'custom' && (
                <Tag
                  closable
                  onClose={() => onTimeRangeChange('')}
                  closeIcon={<CloseCircleOutlined />}
                  color="green"
                >
                  时间: {getTimeRangeLabel()}
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
