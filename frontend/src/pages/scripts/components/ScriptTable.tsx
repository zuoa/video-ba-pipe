import React from 'react';
import { Table, Button, Space, Tag, Empty } from 'antd';
import {
  EditOutlined,
  DeleteOutlined,
  CodeOutlined,
  SettingOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { StatusBadge } from '@/components/common';
import './ScriptTable.css';

export interface ScriptTableProps {
  scripts: any[];
  loading: boolean;
  categories: Array<{ key: string; label: string; icon: React.ReactNode }>;
  activeCategory: string;
  onCategoryChange: (category: string) => void;
  onEdit: (script: any) => void;
  onDelete: (scriptPath: string) => void;
}

const ScriptTable: React.FC<ScriptTableProps> = ({
  scripts,
  loading,
  categories,
  activeCategory,
  onCategoryChange,
  onEdit,
  onDelete,
}) => {
  const getCategoryColor = (category: string) => {
    const colors: Record<string, string> = {
      detectors: 'blue',
      filters: 'green',
      hooks: 'orange',
      postprocessors: 'purple',
    };
    return colors[category] || 'default';
  };

  const getStatusConfig = (status: string) => {
    if (status === 'active') {
      return { text: '已启用', color: 'success' };
    }
    if (status === 'available') {
      return { text: '可用', color: 'processing' };
    }
    return { text: '未注册', color: 'default' };
  };

  const columns = [
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
      title: '类别',
      dataIndex: 'category',
      key: 'category',
      width: 140,
      render: (category: string) => (
        <Tag color={getCategoryColor(category)}>{category || '未知'}</Tag>
      ),
    },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (name: string) => (
        <div className="name-cell">
          <CodeOutlined className="name-icon" />
          <span className="name-text">{name}</span>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (status: string) => {
        const config = getStatusConfig(status);
        return <Tag color={config.color}>{config.text}</Tag>;
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
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
          {record.algorithm_id ? (
            <Button
              size="small"
              icon={<SettingOutlined />}
              className="action-btn action-btn-config"
              title="配置算法"
            >
              配置
            </Button>
          ) : (
            <Button
              size="small"
              icon={<PlusOutlined />}
              className="action-btn action-btn-register"
              title="注册算法"
            >
              注册
            </Button>
          )}
          <Button
            size="small"
            icon={<DeleteOutlined />}
            onClick={() => onDelete(record.path)}
            className="action-btn action-btn-delete"
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="script-table-wrapper">
      {/* 分类标签 */}
      <div className="category-tabs">
        <Space size="small">
          {categories.map((cat) => (
            <button
              key={cat.key}
              className={`category-tab ${activeCategory === cat.key ? 'active' : ''}`}
              onClick={() => onCategoryChange(cat.key)}
            >
              {cat.icon}
              <span>{cat.label}</span>
            </button>
          ))}
        </Space>
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
