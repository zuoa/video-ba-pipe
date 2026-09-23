import { getDateLocale } from '@/i18n/tr';
import { tr, trf } from '@/i18n/tr';
import React from 'react';
import { Table, Space, Tag, Input, message } from 'antd';
import Button from '@/components/common/AppButton';
import AppEmptyState from '@/components/common/AppEmptyState';
import AppToolbar from '@/components/common/AppToolbar';
import {
  EditOutlined,
  DeleteOutlined,
  CodeOutlined,
  SearchOutlined,
  CloudUploadOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons';
import type { ChangeEvent } from 'react';
import { updateScript } from '@/services/api';
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

    if (diffMins < 1) return tr("刚刚");
    if (diffMins < 60) return trf("__VAR0__分钟前", [diffMins]);
    if (diffHours < 24) return trf("__VAR0__小时前", [diffHours]);
    if (diffDays < 7) return trf("__VAR0__天前", [diffDays]);
    return date.toLocaleDateString(getDateLocale());
  };

  const columns = [
    {
      title: tr("脚本名称"),
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
      title: tr("路径"),
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
      title: tr("使用状态"),
      key: 'in_use',
      width: 120,
      render: (_: any, record: any) => (
        record.algorithm_id ? (
          <Tag icon={<CheckCircleOutlined />} color="success">{tr("在用")}</Tag>
        ) : (
          <Tag>{tr("未使用")}</Tag>
        )
      ),
    },
    {
      title: tr("最后修改"),
      dataIndex: 'modified_time',
      key: 'modified_time',
      width: 140,
      render: (timestamp: number) => (
        <span className="time-text">{formatLastModified(timestamp)}</span>
      ),
    },
    {
      title: tr("操作"),
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
            {tr("编辑")}
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
                    try {
                      await updateScript(record.path, { content });
                      message.success(tr("脚本上传成功"));
                    } catch (error: any) {
                      message.error(error?.message || tr("脚本上传失败"));
                    }
                  };
                  reader.readAsText(file);
                }
              };
              input.click();
            }}
            className="action-btn action-btn-upload"
            title={tr("上传文件")}
          >
            {tr("上传")}
          </Button>
          <Button
            size="small"
            icon={<DeleteOutlined />}
            onClick={() => onDelete(record.path)}
            className="action-btn action-btn-delete"
            danger
          >
            {tr("删除")}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="script-table-wrapper">
      {/* 搜索栏 */}
      <AppToolbar className="search-bar">
        <Input
          placeholder={tr("搜索脚本名称或路径...")}
          value={searchText}
          onChange={handleSearchChange}
          prefix={<SearchOutlined />}
          allowClear
          size="large"
          className="search-input"
        />
      </AppToolbar>

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
            showTotal: (total) => trf("共 __VAR0__ 条", [total]),
          }}
          className="script-table"
          locale={{
            emptyText: (
              <AppEmptyState
                compact
                image={<CodeOutlined className="script-empty-icon" />}
                title={tr("暂无脚本")}
              />
            ),
          }}
        />
      </div>
    </div>
  );
};

export default ScriptTable;
