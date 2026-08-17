import React from 'react';
import { Table, Space, Image, Tag, Tooltip } from 'antd';
import Button from '@/components/common/AppButton';
import {
  EditOutlined,
  DeleteOutlined,
  CameraOutlined,
  VideoCameraOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { StatusBadge, SwitchBadge } from '@/components/common';
import './SourceTable.css';

export interface SourceTableProps {
  sources: any[];
  loading: boolean;
  onEdit: (source: any) => void;
  onDelete: (id: number) => void;
  onPreview: (source: any) => void;
  onLivePreview: (source: any) => void;
  webrtcEnabled: boolean;
  onRefreshStatus: (source: any) => void;
  refreshingId: number | null;
}

const SourceTable: React.FC<SourceTableProps> = ({
  sources,
  loading,
  onEdit,
  onDelete,
  onPreview,
  onLivePreview,
  webrtcEnabled,
  onRefreshStatus,
  refreshingId,
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
            <div className="name-text">
              {name}
              {record.license_runtime_allowed === false ? (
                <Tooltip title="该视频源超出当前授权运行范围，配置会保留但不会启动分析">
                  <Tag color="default" style={{ marginLeft: 8 }}>未获运行授权</Tag>
                </Tooltip>
              ) : null}
            </div>
            <div className="name-code">{record.buffer_name}</div>
          </div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 180,
      render: (status: string, record: any) => (
        <Space size={6}>
          <StatusBadge status={status} />
          <Tooltip title="重新探测状态">
            <Button
              type="text"
              size="small"
              className="action-btn refresh-status-btn"
              icon={<ReloadOutlined />}
              loading={refreshingId === record.id}
              onClick={() => onRefreshStatus(record)}
            />
          </Tooltip>
        </Space>
      ),
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
          <div className="source-preview-actions">
            <Tooltip title="WebRTC 实时画面（按需拉流）">
              <Button
                type="text"
                size="small"
                icon={<VideoCameraOutlined />}
                onClick={() => onLivePreview(record)}
                className="live-preview-btn"
                disabled={!webrtcEnabled}
              >
                <span className="live-label">实时预览</span>
                {webrtcEnabled && <span className="live-dot" />}
              </Button>
            </Tooltip>
            <Button
              type="text"
              size="small"
              icon={<CameraOutlined />}
              onClick={() => onPreview(record)}
              className="detection-frame-btn"
            >
              最新检测帧
            </Button>
          </div>
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
