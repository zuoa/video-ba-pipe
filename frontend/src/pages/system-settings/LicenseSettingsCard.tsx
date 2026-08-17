import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Descriptions, Progress, Space, Spin, Tag, Upload, message } from 'antd';
import { SafetyCertificateOutlined, UploadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  getLicenseStatus,
  installLicense,
  LicenseStatus,
} from '@/services/api';
import { LICENSE_UPDATED_EVENT } from '@/components/LicenseBanner';

const quotaPercent = (used: number, limit: number) => (
  limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : (used > 0 ? 100 : 0)
);

const LicenseSettingsCard: React.FC = () => {
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [installing, setInstalling] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await getLicenseStatus());
    } catch (error: any) {
      message.error(`加载许可证状态失败：${error.message || error.error || '未知错误'}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  const handleInstall = async () => {
    if (!file) {
      message.warning('请先选择 .license 文件');
      return;
    }
    setInstalling(true);
    try {
      const nextStatus = await installLicense(file);
      setStatus(nextStatus);
      setFile(null);
      window.dispatchEvent(new Event(LICENSE_UPDATED_EVENT));
      message.success('许可证安装成功，运行服务将自动刷新授权');
    } catch (error: any) {
      message.error(`许可证安装失败：${error.error || error.message || '未知错误'}`);
    } finally {
      setInstalling(false);
    }
  };

  if (loading || !status) return <Spin />;
  const licensed = status.tier === 'licensed';

  return (
    <Space direction="vertical" size={20} style={{ width: '100%' }}>
      <Alert
        showIcon
        type={licensed ? 'success' : 'warning'}
        message={licensed ? '付费许可证有效' : '永久免费试用版'}
        description={licensed
          ? `有效期至 ${dayjs(status.expires_at).format('YYYY-MM-DD HH:mm:ss')}`
          : `${status.message}。系统按固定规则运行最早创建的一路视频源和三个算法。`}
      />

      <Descriptions bordered column={{ xs: 1, sm: 2 }} size="small">
        <Descriptions.Item label="授权层级">
          <Tag color={licensed ? 'green' : 'gold'}>{licensed ? '付费版' : '免费版'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="节点 ID">{status.node_id || '—'}</Descriptions.Item>
        <Descriptions.Item label="客户">{status.customer || '—'}</Descriptions.Item>
        <Descriptions.Item label="许可证编号">{status.license_id || '—'}</Descriptions.Item>
      </Descriptions>

      <div className="license-quota-grid">
        <div className="license-quota-item">
          <strong>视频源</strong>
          <span>{status.usage.video_sources} / {status.limits.video_sources}</span>
          <Progress percent={quotaPercent(status.usage.video_sources, status.limits.video_sources)} status={status.over_limit.video_sources ? 'exception' : 'normal'} />
        </div>
        <div className="license-quota-item">
          <strong>算法</strong>
          <span>{status.usage.algorithms} / {status.limits.algorithms}</span>
          <Progress percent={quotaPercent(status.usage.algorithms, status.limits.algorithms)} status={status.over_limit.algorithms ? 'exception' : 'normal'} />
        </div>
      </div>

      <Space wrap>
        <Upload
          accept=".license,text/plain"
          maxCount={1}
          fileList={file ? [{ uid: file.name, name: file.name, status: 'done', originFileObj: file } as any] : []}
          beforeUpload={(selected) => { setFile(selected); return false; }}
          onRemove={() => { setFile(null); return true; }}
        >
          <Button icon={<UploadOutlined />}>选择许可证</Button>
        </Upload>
        <Button type="primary" icon={<SafetyCertificateOutlined />} loading={installing} onClick={handleInstall}>
          安装 / 续期
        </Button>
      </Space>
    </Space>
  );
};

export default LicenseSettingsCard;
