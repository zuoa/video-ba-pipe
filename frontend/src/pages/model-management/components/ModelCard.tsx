import React from 'react';
import { Card, Space, Typography, Tooltip, message } from 'antd';
import {
  EyeOutlined,
  CopyOutlined,
  DeleteOutlined,
  ApiOutlined,
  ClockCircleOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import AppButton from '@/components/common/AppButton';
import StatusBadge from '@/components/common/StatusBadge';
import { getModel } from '@/services/api';
import { copyToClipboard } from '@/utils/clipboard';
import './ModelCard.css';

const { Text, Paragraph } = Typography;

interface ModelCardProps {
  model: {
    id: number;
    name: string;
    version: string;
    model_type: string;
    model_role?: string;
    framework: string;
    file_size_mb: number;
    input_shape?: string;
    description?: string;
    enabled: boolean;
    usage_count: number;
    created_at: string;
    quick_setup?: {
      eligible: boolean;
      reason?: string | null;
    };
  };
  onView: (model: any) => void;
  onDelete: (id: number) => void;
  onQuickSetup: (model: any) => void;
  onConfigure: (model: any) => void;
}

const ModelCard: React.FC<ModelCardProps> = ({
  model,
  onView,
  onDelete,
  onQuickSetup,
  onConfigure,
}) => {
  const handleCopyPath = async () => {
    try {
      const data = await getModel(model.id);
      const ok = await copyToClipboard(data.model.file_path);
      if (ok) {
        message.success('模型路径已复制');
      } else {
        message.error('复制路径失败');
      }
    } catch (error) {
      message.error('复制路径失败');
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
        {model.model_type === 'OCR' ? (
          <div className="info-item">
            <span className="info-label">角色</span>
            <span className="info-value">{model.model_role === 'detection' ? '文字检测' : '文字识别'}</span>
          </div>
        ) : null}
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
        <Paragraph
          type="secondary"
          ellipsis={{ rows: 2 }}
          className="model-description"
        >
          {model.description}
        </Paragraph>
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
          {model.quick_setup?.eligible ? (
            <Tooltip title="用通用脚本创建算法和告警模板">
              <AppButton
                size="small"
                tone="success"
                iconOnly
                className="action-btn action-btn-quick"
                aria-label={`从模型 ${model.name} 快速创建算法和模板`}
                onClick={() => onQuickSetup(model)}
              >
                <ThunderboltOutlined />
              </AppButton>
            </Tooltip>
          ) : (
            <Tooltip title={model.quick_setup?.reason || '使用完整向导配置算法'}>
              <AppButton
                size="small"
                iconOnly
                className="action-btn action-btn-configure"
                aria-label={`为模型 ${model.name} 配置算法`}
                onClick={() => onConfigure(model)}
              >
                <SettingOutlined />
              </AppButton>
            </Tooltip>
          )}
          <Tooltip title="查看详情">
            <AppButton
              size="small"
              tone="info"
              className="action-btn action-btn-view"
              onClick={() => onView(model)}
            >
              <EyeOutlined />
              <span>详情</span>
            </AppButton>
          </Tooltip>
          <Tooltip title="复制路径">
            <AppButton
              size="small"
              iconOnly
              className="action-btn action-btn-copy"
              aria-label={`复制模型 ${model.name} 的路径`}
              onClick={handleCopyPath}
            >
              <CopyOutlined />
            </AppButton>
          </Tooltip>
          {model.usage_count === 0 && (
            <Tooltip title="删除模型">
              <AppButton
                size="small"
                tone="danger"
                iconOnly
                className="action-btn action-btn-delete"
                aria-label={`删除模型 ${model.name}`}
                onClick={() => onDelete(model.id)}
              >
                <DeleteOutlined />
              </AppButton>
            </Tooltip>
          )}
        </Space>
      </div>
    </Card>
  );
};

export default ModelCard;
