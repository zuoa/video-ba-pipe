import React from 'react';
import { Descriptions } from 'antd';
import AppModal from '@/components/common/AppModal';
import { StatusBadge } from '@/components/common';

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
  is_healthy?: boolean;
  error?: string;
  _name?: string;
}

interface SourceHealthModalProps {
  open: boolean;
  detail: SourceHealthDetail | null;
  onClose: () => void;
}

function frameColor(t: number | null | undefined): string {
  if (typeof t !== 'number') return 'inherit';
  if (t > NO_FRAME_CRITICAL) return '#cf1322';
  if (t > NO_FRAME_WARNING) return '#d48806';
  return '#389e0d';
}

const SourceHealthModal: React.FC<SourceHealthModalProps> = ({
  open,
  detail,
  onClose,
}) => {
  const t = detail?.time_since_last_frame;
  const color = frameColor(t);

  return (
    <AppModal
      kind="detail"
      size="sm"
      title="实时状态探测"
      description={detail?._name || detail?.name}
      open={open}
      onCancel={onClose}
      footer={null}
      maskClosable
    >
      {detail && (
        <Descriptions column={2} size="small" bordered colon={false}>
          <Descriptions.Item label="综合健康">
            <StatusBadge
              tone={detail.is_healthy ? 'success' : 'danger'}
              text={detail.is_healthy ? '健康' : '异常'}
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
