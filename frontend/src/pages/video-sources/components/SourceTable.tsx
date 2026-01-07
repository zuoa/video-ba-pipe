import React from 'react';
import { Table, Button, Space, Image } from 'antd';
import {
  EditOutlined,
  DeleteOutlined,
  EyeOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { StatusBadge, SwitchBadge } from '@/components/common';
import './SourceTable.css';

export interface SourceTableProps {
  sources: any[];
  loading: boolean;
  onEdit: (source: any) => void;
  onDelete: (id: number) => void;
  onPreview: (source: any) => void;
}

const SourceTable: React.FC<SourceTableProps> = ({
  sources,
  loading,
  onEdit,
  onDelete,
  onPreview,
}) => {
  const columns = [
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
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 280,
      render: (name: string, record: any) => (
        <div className="name-cell">
          <div className="name-icon">
            <VideoCameraOutlined />
          </div>
          <div className="name-content">
            <div className="name-text">{name}</div>
            <div className="name-code">{record.buffer_name}</div>
          </div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 140,
      render: (status: string) => <StatusBadge status={status} />,
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 140,
      render: (enabled: boolean, record: any) => (
        <SwitchBadge
          checked={enabled}
          checkedText="启用"
          uncheckedText="禁用"
          size="small"
        />
      ),
    },
    {
      title: '源信息',
      key: 'sourceInfo',
      width: 300,
      render: (_: any, record: any) => (
        <div className="source-info-cell">
          <div className="source-info-content">
            <div className="source-name">{record.name || '未命名'}</div>
            <div className="source-code">
              <span className="code-icon">⚡</span>
              {record.source_code}
            </div>
          </div>
          <Button
            type="text"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => onPreview(record)}
            className="preview-btn"
          >
            预览
          </Button>
        </div>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_: any, record: any) => (
        <Space size="small">
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => onEdit(record)}
            className="action-btn action-btn-edit"
          >
            编辑
          </Button>
          <Button
            size="small"
            icon={<DeleteOutlined />}
            onClick={() => onDelete(record.id)}
            className="action-btn action-btn-delete"
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="source-table-wrapper">
      <Table
        dataSource={sources}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={{
          pageSize: 10,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条`,
        }}
        className="source-table"
      />
    </div>
  );
};

export default SourceTable;
