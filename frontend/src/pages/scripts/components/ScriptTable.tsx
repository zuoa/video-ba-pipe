import React from 'react';
import { Table, Button, Space, Tag, Empty, Input } from 'antd';
import {
  EditOutlined,
  DeleteOutlined,
  CodeOutlined,
  SearchOutlined,
  CloudUploadOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons';
import type { ChangeEvent } from 'react';
import './ScriptTable.css';

export interface ScriptTableProps {
  scripts: any[];
  loading: boolean;
  searchText: string;
  onSearchChange: (value: string) => void;
  onEdit: (script: any) => void;
  onDelete: (scriptPath: string) => void;
}

const ScriptTable: React.FC<ScriptTableProps> = ({
  scripts,
  loading,
  searchText,
  onSearchChange,
  onEdit,
  onDelete,
}) => {
  const handleSearchChange = (e: ChangeEvent<HTMLInputElement>) => {
    onSearchChange(e.target.value);
  };

  const formatLastModified = (timestamp: number) => {
    if (!timestamp) return '-';
    const date = new Date(timestamp * 1000);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return '刚刚';
    if (diffMins < 60) return `${diffMins}分钟前`;
    if (diffHours < 24) return `${diffHours}小时前`;
    if (diffDays < 7) return `${diffDays}天前`;
    return date.toLocaleDateString('zh-CN');
  };

  const columns = [
    {
      title: '脚本名称',
      dataIndex: 'name',
      key: 'name',
      width: 250,
      render: (name: string, record: any) => (
        <div className="name-cell">
          <CodeOutlined className="name-icon" />
          <div className="name-info">
            <span className="name-text">{name || record.path}</span>
            {record.path && name !== record.path && (
              <code className="path-code">{record.path}</code>
            )}
          </div>
        </div>
      ),
    },
    {
      title: '路径',
      dataIndex: 'path',
      key: 'path',
      width: 300,
      render: (path: string) => (
        <div className="path-cell">
          <code className="path-code">{path}</code>
        </div>
      ),
    },
    {
      title: '使用状态',
      key: 'in_use',
      width: 120,
      render: (_: any, record: any) => (
        record.algorithm_id ? (
          <Tag icon={<CheckCircleOutlined />} color="success">在用</Tag>
        ) : (
          <Tag>未使用</Tag>
        )
      ),
    },
    {
      title: '最后修改',
      dataIndex: 'modified_time',
      key: 'modified_time',
      width: 140,
      render: (timestamp: number) => (
        <span className="time-text">{formatLastModified(timestamp)}</span>
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
            icon={<CloudUploadOutlined />}
            onClick={() => {
              // 触发文件上传对话框
              const input = document.createElement('input');
              input.type = 'file';
              input.accept = '.py';
              input.onchange = async (e) => {
                const file = (e.target as HTMLInputElement).files?.[0];
                if (file) {
                  const reader = new FileReader();
                  reader.onload = async (event) => {
                    const content = event.target?.result as string;
                    // TODO: 调用上传 API
                    await fetch(`/api/scripts/${record.path}`, {
                      method: 'PUT',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ content })
                    });
                  };
                  reader.readAsText(file);
                }
              };
              input.click();
            }}
            className="action-btn action-btn-upload"
            title="上传文件"
          >
            上传
          </Button>
          <Button
            size="small"
            icon={<DeleteOutlined />}
            onClick={() => onDelete(record.path)}
            className="action-btn action-btn-delete"
            danger
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="script-table-wrapper">
      {/* 搜索栏 */}
      <div className="search-bar">
        <Input
          placeholder="搜索脚本名称或路径..."
          value={searchText}
          onChange={handleSearchChange}
          prefix={<SearchOutlined />}
          allowClear
          size="large"
          className="search-input"
        />
      </div>

      {/* 表格 */}
      <div className="table-container">
        <Table
          dataSource={scripts}
          columns={columns}
          rowKey="path"
          loading={loading}
          pagination={{
            pageSize: 20,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total) => `共 ${total} 条`,
          }}
          className="script-table"
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  <div className="empty-state">
                    <CodeOutlined style={{ fontSize: 48, color: '#d9d9d9' }} />
                    <p style={{ marginTop: 16, color: '#8c8c8c' }}>暂无脚本</p>
                  </div>
                }
              />
            ),
          }}
        />
      </div>
    </div>
  );
};

export default ScriptTable;
