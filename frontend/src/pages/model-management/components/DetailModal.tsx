import React, { useState } from 'react';
import { Descriptions, Space, Tag, message } from 'antd';
import Button from '@/components/common/AppButton';
import { DownloadOutlined, CopyOutlined } from '@ant-design/icons';
import { downloadModelFile } from '@/services/api';
import AppModal from '@/components/common/AppModal';

interface Model {
  id: number;
  name: string;
  version: string;
  model_type: string;
  model_role?: string;
  framework: string;
  filename: string;
  file_path: string;
  file_size_mb: number;
  input_shape?: string;
  model_postprocess?: Record<string, any> | null;
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
  const [downloading, setDownloading] = useState(false);

  if (!model) return null;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(model.file_path);
      message.success('路径已复制到剪贴板');
    } catch (error) {
      message.error('复制失败');
    }
  };

  const handleDownload = async () => {
    setDownloading(true);
    try {
      await downloadModelFile(model.id);
    } catch (error: any) {
      message.error(error?.message || '下载失败');
    } finally {
      setDownloading(false);
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
      second: '2-digit',
    });
  };

  return (
    <AppModal
      title="模型详情"
      description={model.name}
      kind="detail"
      size="md"
      open={visible}
      onCancel={onClose}
      footer={
        <Space>
          <Button icon={<CopyOutlined />} onClick={handleCopy}>
            复制路径
          </Button>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            onClick={handleDownload}
            loading={downloading}
            disabled={downloading}
          >
            下载模型
          </Button>
        </Space>
      }
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
        {model.model_type === 'OCR' ? (
          <Descriptions.Item label="OCR 角色" span={2}>
            <Tag color={model.model_role === 'detection' ? 'cyan' : 'geekblue'}>
              {model.model_role === 'detection' ? '文字检测' : '文字识别'}
            </Tag>
          </Descriptions.Item>
        ) : null}
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
        {model.model_postprocess && (
          <Descriptions.Item label="后处理配置" span={2}>
            <pre style={{
              margin: 0,
              padding: '8px 10px',
              background: '#fafafa',
              borderRadius: 4,
              fontSize: 12,
              overflowX: 'auto'
            }}>
              {JSON.stringify(model.model_postprocess, null, 2)}
            </pre>
          </Descriptions.Item>
        )}
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
    </AppModal>
  );
};

export default DetailModal;
