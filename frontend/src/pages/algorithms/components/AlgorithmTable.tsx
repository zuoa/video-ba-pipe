import React from 'react';
import { Table, Space, Tag, Tooltip } from 'antd';
import {
  EditOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  ExperimentOutlined,
  ApiOutlined,
  RobotOutlined,
  FileSearchOutlined,
  ApartmentOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import AppButton from '@/components/common/AppButton';
import './AlgorithmTable.css';

export interface Algorithm {
  id: number;
  name: string;
  algorithm_type?: 'script' | 'vl' | 'ocr' | 'cascade';
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
  license_runtime_allowed?: boolean;
  vl_config?: {
    base_url?: string;
    model_name?: string;
    api_key_configured?: boolean;
  };
  ocr_config?: {
    detection_model_id?: number;
    recognition_model_id?: number;
    device?: string;
  };
  cascade_config?: {
    version?: number;
    stages?: Array<{ id: string; name: string; model_id: number }>;
    output?: { label?: string; color?: string };
    nodes?: Array<{ id: string; type: string; name: string; label?: string }>;
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
          <div className={`algorithm-icon ${record.algorithm_type === 'vl' ? 'algorithm-icon-vl' : ''} ${record.algorithm_type === 'cascade' ? 'algorithm-icon-cascade' : ''}`}>
            {record.algorithm_type === 'vl'
              ? <RobotOutlined />
              : record.algorithm_type === 'ocr'
                ? <FileSearchOutlined />
                : record.algorithm_type === 'cascade'
                  ? <ApartmentOutlined />
                : <ExperimentOutlined />}
          </div>
          <div className="algorithm-content">
            <div className="algorithm-name">
              {record.name}
              <Tag
                color={record.algorithm_type === 'vl' ? 'cyan' : record.algorithm_type === 'ocr' ? 'blue' : record.algorithm_type === 'cascade' ? 'gold' : 'purple'}
                className="algorithm-type-tag"
              >
                {record.algorithm_type === 'vl' ? 'VL' : record.algorithm_type === 'ocr' ? 'OCR' : record.algorithm_type === 'cascade' ? '组合检测' : '脚本'}
              </Tag>
              {record.license_runtime_allowed === false ? (
                <Tooltip title="该算法超出当前授权运行范围，配置会保留但不会执行">
                  <Tag color="default">未获运行授权</Tag>
                </Tooltip>
              ) : null}
            </div>
            {record.description && (
              <div className="algorithm-description">{record.description}</div>
            )}
            <div className="algorithm-meta">
              <Tooltip title={
                record.algorithm_type === 'vl'
                  ? record.vl_config?.base_url
                  : record.algorithm_type === 'ocr'
                    ? `运行设备：${record.ocr_config?.device || 'auto'}`
                    : record.algorithm_type === 'cascade'
                      ? record.cascade_config?.version === 2
                        ? record.cascade_config?.nodes?.filter(node => node.type === 'detector').map(node => node.name).join(' · ')
                        : record.cascade_config?.stages?.map(stage => stage.name).join(' → ')
                    : record.script_path
              }>
                <code className="algorithm-code">
                  <ApiOutlined />
                  {record.algorithm_type === 'vl'
                    ? `${record.vl_config?.model_name || '未配置模型'} · ${record.vl_config?.base_url || '未配置接口'}`
                    : record.algorithm_type === 'ocr'
                      ? `检测模型 #${record.ocr_config?.detection_model_id || '-'} · 识别模型 #${record.ocr_config?.recognition_model_id || '-'}`
                      : record.algorithm_type === 'cascade'
                        ? record.cascade_config?.version === 2
                          ? `${record.cascade_config?.nodes?.filter(node => node.type === 'detector').length || 0} 个检测 · ${record.cascade_config?.nodes?.find(node => node.type === 'output')?.label || '未配置输出'}`
                          : `${record.cascade_config?.stages?.length || 0} 阶段 · ${record.cascade_config?.output?.label || '未配置输出'}`
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
            <AppButton
              size="small"
              tone="info"
              className="action-btn action-btn-test"
              onClick={() => onTest(record)}
            >
              <PlayCircleOutlined />
              <span>测试</span>
            </AppButton>
          </Tooltip>
          <Tooltip title="编辑算法">
            <AppButton
              size="small"
              tone="info"
              className="action-btn action-btn-edit"
              onClick={() => onEdit(record)}
            >
              <EditOutlined />
              <span>编辑</span>
            </AppButton>
          </Tooltip>
          <Tooltip title="删除算法">
            <AppButton
              size="small"
              tone="danger"
              className="action-btn action-btn-delete"
              onClick={() => onDelete(record.id)}
            >
              <DeleteOutlined />
              <span>删除</span>
            </AppButton>
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
