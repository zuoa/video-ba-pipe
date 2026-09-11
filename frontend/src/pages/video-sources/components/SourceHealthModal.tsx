import React from 'react';
import { Descriptions, Space } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import AppModal from '@/components/common/AppModal';
import Button from '@/components/common/AppButton';
import { StatusBadge } from '@/components/common';
import type { SemanticTone } from '@/components/common/AppButton';

// 与后端 NO_FRAME_WARNING_THRESHOLD / NO_FRAME_CRITICAL_THRESHOLD 对齐（app/config.py）
const NO_FRAME_WARNING = 15;
const NO_FRAME_CRITICAL = 30;

export interface SourceHealthDetail {
  source_id?: number;
  name?: string;
  status?: string;
  enabled?: boolean;
  last_write_time?: number | null;
  time_since_last_frame?: number | null;
  consecutive_errors?: number;
  frame_count?: number;
  is_healthy?: boolean | null;
  health_state?: string;
  error?: string | null;
  probed_at?: number;
  _name?: string;
}

interface SourceHealthModalProps {
  open: boolean;
  detail: SourceHealthDetail | null;
  onClose: () => void;
  onRetry: () => void;
  retrying: boolean;
}

function frameColor(t: number | null | undefined): string {
  if (typeof t !== 'number') return 'inherit';
  if (t > NO_FRAME_CRITICAL) return '#cf1322';
  if (t > NO_FRAME_WARNING) return '#d48806';
  return '#389e0d';
}

function healthBadge(detail: SourceHealthDetail): {
  tone: SemanticTone | 'muted';
  text: string;
} {
  if (detail.is_healthy === true) return { tone: 'success', text: '健康' };
  if (detail.is_healthy === false) return { tone: 'danger', text: '异常' };
  if (detail.health_state === 'pending') return { tone: 'info', text: '启动中' };
  if (detail.health_state === 'inactive') return { tone: 'muted', text: '未运行' };
  return { tone: 'muted', text: '暂无数据' };
}

const SourceHealthModal: React.FC<SourceHealthModalProps> = ({
  open,
  detail,
  onClose,
  onRetry,
  retrying,
}) => {
  const t = detail?.time_since_last_frame;
  const color = frameColor(t);
  const health = detail ? healthBadge(detail) : null;

  return (
    <AppModal
      kind="detail"
      size="sm"
      title="实时状态探测"
      description={detail?._name || detail?.name}
      open={open}
      onCancel={onClose}
      footer={(
        <Space>
          <Button onClick={onClose}>关闭</Button>
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            loading={retrying}
            onClick={onRetry}
          >
            重新探测
          </Button>
        </Space>
      )}
      maskClosable
    >
      {detail && (
        <Descriptions column={2} size="small" bordered colon={false}>
          <Descriptions.Item label="综合健康">
            <StatusBadge
              tone={health?.tone}
              text={health?.text}
            />
          </Descriptions.Item>
          <Descriptions.Item label="运行状态">
            <StatusBadge status={detail.status || 'UNKNOWN'} />
          </Descriptions.Item>
          <Descriptions.Item label="距上一帧" span={2}>
            <span style={{ color, fontWeight: 600 }}>
              {typeof t === 'number' ? `${t.toFixed(1)} 秒` : '—'}
            </span>
            <span style={{ color: '#999', marginLeft: 8, fontSize: 12 }}>
              （&gt;{NO_FRAME_WARNING}s 预警，&gt;{NO_FRAME_CRITICAL}s 危险）
            </span>
          </Descriptions.Item>
          <Descriptions.Item label="累计帧数">
            {detail.frame_count ?? '—'}
          </Descriptions.Item>
          <Descriptions.Item label="连续错误">
            {detail.consecutive_errors ?? '—'}
          </Descriptions.Item>
          <Descriptions.Item label="启用" span={2}>
            {detail.enabled ? '启用' : '禁用'}
          </Descriptions.Item>
          {detail.probed_at && (
            <Descriptions.Item label="探测时间" span={2}>
              {new Date(detail.probed_at * 1000).toLocaleString()}
            </Descriptions.Item>
          )}
          {detail.error && (
            <Descriptions.Item label="异常信息" span={2}>
              <span style={{ color: '#cf1322' }}>{detail.error}</span>
            </Descriptions.Item>
          )}
        </Descriptions>
      )}
    </AppModal>
  );
};

export default SourceHealthModal;
