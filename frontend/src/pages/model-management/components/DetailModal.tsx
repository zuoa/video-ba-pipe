import React from 'react';
import { Modal, Descriptions, Button, Space, message } from 'antd';
import { DownloadOutlined, CopyOutlined, InfoCircleOutlined } from '@ant-design/icons';

interface Model {
  id: number;
  name: string;
  version: string;
  model_type: string;
  framework: string;
  filename: string;
  file_path: string;
  file_size_mb: number;
  input_shape?: string;
  description?: string;
  enabled: boolean;
  usage_count: number;
  download_count: number;
  created_at: string;
}

interface DetailModalProps {
  visible: boolean;
  model: Model | null;
  onClose: () => void;
}

const DetailModal: React.FC<DetailModalProps> = ({ visible, model, onClose }) => {
  if (!model) return null;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(model.file_path);
      message.success('路径已复制到剪贴板');
    } catch (error) {
      message.error('复制失败');
    }
  };

  const handleDownload = () => {
    window.open(`/api/models/${model.id}/download`, '_blank');
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  return (
    <Modal
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div
            style={{
              width: 40,
              height: 40,
              background: 'linear-gradient(135deg, #000000 0%, #333333 100%)',
              borderRadius: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
            }}
          >
            <InfoCircleOutlined />
          </div>
          <span>模型详情</span>
        </div>
      }
      open={visible}
      onCancel={onClose}
      footer={
        <Space>
          <Button icon={<CopyOutlined />} onClick={handleCopy}>
            复制路径
          </Button>
          <Button type="primary" icon={<DownloadOutlined />} onClick={handleDownload}>
            下载模型
          </Button>
        </Space>
      }
      width={640}
    >
      <Descriptions column={2} bordered size="small">
        <Descriptions.Item label="模型名称" span={2}>
          <span style={{ fontSize: 16, fontWeight: 600 }}>{model.name}</span>
        </Descriptions.Item>
        <Descriptions.Item label="版本">{model.version || 'v1.0'}</Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={model.enabled ? 'green' : 'default'}>
            {model.enabled ? '启用' : '禁用'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="类型">{model.model_type}</Descriptions.Item>
        <Descriptions.Item label="框架">{model.framework}</Descriptions.Item>
        <Descriptions.Item label="文件名" span={2}>
          <span style={{
            fontFamily: 'monospace',
            background: '#fafafa',
            padding: '4px 8px',
            borderRadius: 4,
            fontSize: 12
          }}>
            {model.filename}
          </span>
        </Descriptions.Item>
        <Descriptions.Item label="文件大小">{model.file_size_mb} MB</Descriptions.Item>
        <Descriptions.Item label="输入尺寸">{model.input_shape || '-'}</Descriptions.Item>
        <Descriptions.Item label="下载次数">{model.download_count || 0}</Descriptions.Item>
        <Descriptions.Item label="使用次数">{model.usage_count || 0}</Descriptions.Item>
        <Descriptions.Item label="上传时间" span={2}>
          {formatDate(model.created_at)}
        </Descriptions.Item>
        {model.description && (
          <Descriptions.Item label="描述" span={2}>
            <div style={{ lineHeight: 1.8 }}>{model.description}</div>
          </Descriptions.Item>
        )}
        <Descriptions.Item label="文件路径" span={2}>
          <span style={{
            fontFamily: 'monospace',
            background: '#fafafa',
            padding: '4px 8px',
            borderRadius: 4,
            fontSize: 12,
            display: 'block',
            wordBreak: 'break-all'
          }}>
            {model.file_path}
          </span>
        </Descriptions.Item>
      </Descriptions>
    </Modal>
  );
};

export default DetailModal;
