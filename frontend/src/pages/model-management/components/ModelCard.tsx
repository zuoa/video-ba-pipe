import React from 'react';
import { Card, Space, Typography, Tooltip, Popconfirm } from 'antd';
import {
  EyeOutlined,
  CopyOutlined,
  DeleteOutlined,
  ApiOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import { StatusBadge } from '@/components/common';
import './ModelCard.css';

const { Text } = Typography;

interface ModelCardProps {
  model: {
    id: number;
    name: string;
    version: string;
    model_type: string;
    framework: string;
    file_size_mb: number;
    input_shape?: string;
    description?: string;
    enabled: boolean;
    usage_count: number;
    created_at: string;
  };
  onView: (model: any) => void;
  onDelete: (id: number) => void;
}

const ModelCard: React.FC<ModelCardProps> = ({ model, onView, onDelete }) => {
  const handleCopyPath = async () => {
    try {
      const response = await fetch(`/api/models/${model.id}`);
      const data = await response.json();
      await navigator.clipboard.writeText(data.model.file_path);
      // 使用轻量提示
    } catch (error) {
      console.error('复制失败:', error);
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <Card
      hoverable
      className="model-card"
      styles={{
        body: { padding: '20px' },
      }}
    >
      {/* 头部 */}
      <div className="model-card-header">
        <div className="model-card-icon">
          <ApiOutlined />
        </div>
        <div className="model-card-title">
          <div className="model-name">{model.name}</div>
          <Text type="secondary" className="model-version">
            {model.version}
          </Text>
        </div>
        <StatusBadge status={model.enabled ? 'ENABLED' : 'DISABLED'} />
      </div>

      {/* 信息列表 */}
      <div className="model-card-info">
        <div className="info-item">
          <span className="info-label">类型</span>
          <span className="info-value">{model.model_type}</span>
        </div>
        <div className="info-item">
          <span className="info-label">框架</span>
          <span className="info-value">{model.framework}</span>
        </div>
        <div className="info-item">
          <span className="info-label">大小</span>
          <span className="info-value">{model.file_size_mb} MB</span>
        </div>
        <div className="info-item">
          <span className="info-label">使用</span>
          <span className="info-value">{model.usage_count} 次</span>
        </div>
        {model.input_shape && (
          <div className="info-item">
            <span className="info-label">输入</span>
            <span className="info-value">{model.input_shape}</span>
          </div>
        )}
      </div>

      {/* 描述 */}
      {model.description && (
        <Text
          type="secondary"
          ellipsis={{ rows: 2 }}
          className="model-description"
        >
          {model.description}
        </Text>
      )}

      {/* 底部 */}
      <div className="model-card-footer">
        <Space size="small" className="footer-time">
          <ClockCircleOutlined />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {formatDate(model.created_at)}
          </Text>
        </Space>
        <Space size="small">
          <Tooltip title="查看详情">
            <button
              type="button"
              className="action-btn action-btn-view"
              onClick={() => onView(model)}
            >
              <EyeOutlined />
              <span>详情</span>
            </button>
          </Tooltip>
          <Tooltip title="复制路径">
            <button
              type="button"
              className="action-btn action-btn-copy"
              onClick={handleCopyPath}
            >
              <CopyOutlined />
            </button>
          </Tooltip>
          {model.usage_count === 0 && (
            <Popconfirm
              title="确定要删除这个模型吗？"
              description="此操作不可恢复"
              onConfirm={() => onDelete(model.id)}
              okText="确定"
              cancelText="取消"
            >
              <button
                type="button"
                className="action-btn action-btn-delete"
              >
                <DeleteOutlined />
              </button>
            </Popconfirm>
          )}
        </Space>
      </div>
    </Card>
  );
};

export default ModelCard;
