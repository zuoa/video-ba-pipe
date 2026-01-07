import React from 'react';
import { Table, Space, Tag, Tooltip } from 'antd';
import {
  EditOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  ExperimentOutlined,
  TagOutlined,
  ClockCircleOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import './AlgorithmTable.css';

export interface Algorithm {
  id: number;
  name: string;
  label_name: string;
  interval_seconds: number;
  plugin_module?: string;
  script_path?: string;
  entry_function?: string;
  enable_window_check: boolean;
  window_size?: number;
  window_mode?: string;
  window_threshold?: number;
  runtime_timeout?: number;
  memory_limit_mb?: number;
  ext_config_json?: string;
  created_at?: string;
}

export interface AlgorithmTableProps {
  algorithms: Algorithm[];
  loading: boolean;
  onEdit: (algorithm: Algorithm) => void;
  onDelete: (id: number) => void;
  onTest: (algorithm: Algorithm) => void;
}

const AlgorithmTable: React.FC<AlgorithmTableProps> = ({
  algorithms,
  loading,
  onEdit,
  onDelete,
  onTest,
}) => {
  const columns: ColumnsType<Algorithm> = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 80,
      render: (id: number) => (
        <div className="id-badge">
          <span>{id}</span>
        </div>
      ),
    },
    {
      title: '算法信息',
      key: 'algorithmInfo',
      width: 280,
      render: (_: any, record: Algorithm) => (
        <div className="algorithm-info-cell">
          <div className="algorithm-icon">
            <ExperimentOutlined />
          </div>
          <div className="algorithm-content">
            <div className="algorithm-name">{record.name}</div>
            <div className="algorithm-meta">
              {record.script_path ? (
                <Tooltip title={record.script_path}>
                  <code className="algorithm-code">
                    <ApiOutlined />
                    {record.script_path}
                  </code>
                </Tooltip>
              ) : record.plugin_module ? (
                <Tooltip title={record.plugin_module}>
                  <code className="algorithm-code">
                    <ApiOutlined />
                    {record.plugin_module}
                  </code>
                </Tooltip>
              ) : (
                <span className="algorithm-code-empty">未配置</span>
              )}
            </div>
          </div>
        </div>
      ),
    },
    {
      title: '标签配置',
      key: 'labelConfig',
      width: 160,
      render: (_: any, record: Algorithm) => (
        <div className="label-config-cell">
          <Tag icon={<TagOutlined />} color="blue" className="label-tag">
            {record.label_name || 'Object'}
          </Tag>
        </div>
      ),
    },
    {
      title: '检测间隔',
      key: 'interval',
      width: 120,
      render: (_: any, record: Algorithm) => (
        <div className="interval-cell">
          <ClockCircleOutlined className="interval-icon" />
          <span className="interval-value">{record.interval_seconds}s</span>
        </div>
      ),
    },
    {
      title: '窗口检测',
      key: 'windowCheck',
      width: 140,
      render: (_: any, record: Algorithm) => (
        <div className="window-check-cell">
          {record.enable_window_check ? (
            <Tag color="green" className="window-enabled-tag">
              <Tooltip title={`窗口: ${record.window_size}s | 模式: ${record.window_mode} | 阈值: ${record.window_threshold}`}>
                已启用
              </Tooltip>
            </Tag>
          ) : (
            <Tag color="default" className="window-disabled-tag">
              未启用
            </Tag>
          )}
        </div>
      ),
    },
    {
      title: '资源配置',
      key: 'resources',
      width: 160,
      render: (_: any, record: Algorithm) => (
        <div className="resources-cell">
          <div className="resource-item">
            <span className="resource-label">超时:</span>
            <span className="resource-value">{record.runtime_timeout || 30}s</span>
          </div>
          <div className="resource-item">
            <span className="resource-label">内存:</span>
            <span className="resource-value">{record.memory_limit_mb || 512}MB</span>
          </div>
        </div>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      fixed: 'right',
      render: (_: any, record: Algorithm) => (
        <Space size="small">
          <Tooltip title="测试算法">
            <button
              className="action-btn action-btn-test"
              onClick={() => onTest(record)}
            >
              <PlayCircleOutlined />
              <span>测试</span>
            </button>
          </Tooltip>
          <Tooltip title="编辑算法">
            <button
              className="action-btn action-btn-edit"
              onClick={() => onEdit(record)}
            >
              <EditOutlined />
              <span>编辑</span>
            </button>
          </Tooltip>
          <Tooltip title="删除算法">
            <button
              className="action-btn action-btn-delete"
              onClick={() => onDelete(record.id)}
            >
              <DeleteOutlined />
              <span>删除</span>
            </button>
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div className="algorithm-table-wrapper">
      <Table
        dataSource={algorithms}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={{
          pageSize: 10,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条`,
          pageSizeOptions: ['10', '20', '50', '100'],
        }}
        className="algorithm-table"
        scroll={{ x: 1200 }}
      />
    </div>
  );
};

export default AlgorithmTable;
