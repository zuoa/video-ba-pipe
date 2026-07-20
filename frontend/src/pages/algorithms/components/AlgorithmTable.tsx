import React from 'react';
import { Table, Space, Tag, Tooltip } from 'antd';
import {
  EditOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  ExperimentOutlined,
  ApiOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import './AlgorithmTable.css';

export interface Algorithm {
  id: number;
  name: string;
  algorithm_type?: 'script' | 'vl';
  description?: string;
  script_path: string;
  script_config?: string;
  enabled_hooks?: string;
  label_name?: string;
  label_color?: string;
  interval_seconds?: number;
  runtime_timeout?: number;
  memory_limit_mb?: number;
  enable_window_check?: boolean;
  window_size?: number;
  window_mode?: string;
  window_threshold?: number;
  created_at?: string;
  updated_at?: string;
  vl_config?: {
    base_url?: string;
    model_name?: string;
    api_key_configured?: boolean;
  };
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
      width: 360,
      render: (_: any, record: Algorithm) => (
        <div className="algorithm-info-cell">
          <div className={`algorithm-icon ${record.algorithm_type === 'vl' ? 'algorithm-icon-vl' : ''}`}>
            {record.algorithm_type === 'vl' ? <RobotOutlined /> : <ExperimentOutlined />}
          </div>
          <div className="algorithm-content">
            <div className="algorithm-name">
              {record.name}
              <Tag color={record.algorithm_type === 'vl' ? 'cyan' : 'purple'} className="algorithm-type-tag">
                {record.algorithm_type === 'vl' ? 'VL' : '脚本'}
              </Tag>
            </div>
            {record.description && (
              <div className="algorithm-description">{record.description}</div>
            )}
            <div className="algorithm-meta">
              <Tooltip title={record.algorithm_type === 'vl' ? record.vl_config?.base_url : record.script_path}>
                <code className="algorithm-code">
                  <ApiOutlined />
                  {record.algorithm_type === 'vl'
                    ? `${record.vl_config?.model_name || '未配置模型'} · ${record.vl_config?.base_url || '未配置接口'}`
                    : record.script_path}
                </code>
              </Tooltip>
            </div>
          </div>
        </div>
      ),
    },
    {
      title: '创建时间',
      key: 'createdAt',
      width: 180,
      render: (_: any, record: Algorithm) => (
        <div className="date-cell">
          {record.created_at ? new Date(record.created_at).toLocaleString('zh-CN') : '-'}
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
