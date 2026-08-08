import React, { useState, useEffect, useRef } from 'react';
import { Button, Tooltip } from 'antd';
import { ReloadOutlined, CloseOutlined, WarningOutlined, CameraOutlined } from '@ant-design/icons';
import AppModal from '@/components/common/AppModal';
import './DetectionFrameModal.css';

export interface DetectionFrameModalProps {
  open: boolean;
  sourceCode?: string;
  name?: string;
  onClose: () => void;
}

const AUTO_REFRESH_MS = 4000;

const DetectionFrameModal: React.FC<DetectionFrameModalProps> = ({ open, sourceCode, name, onClose }) => {
  const [refreshKey, setRefreshKey] = useState(0);
  const [phase, setPhase] = useState<'loading' | 'detection' | 'fallback' | 'error'>('loading');
  const triedFallbackRef = useRef(false);

  const primarySrc = sourceCode
    ? `/api/image/detection_snapshots/${sourceCode}.jpg?t=${refreshKey}`
    : '';
  const fallbackSrc = sourceCode
    ? `/api/image/snapshots/${sourceCode}.jpg?t=${refreshKey}`
    : '';

  const refresh = () => {
    triedFallbackRef.current = false;
    setPhase('loading');
    setRefreshKey((k) => k + 1);
  };

  useEffect(() => {
    if (open) {
      refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sourceCode]);

  useEffect(() => {
    if (!open) return;
    const timer = setInterval(() => {
      triedFallbackRef.current = false;
      setRefreshKey((k) => k + 1);
      setPhase('loading');
    }, AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [open]);

  const handlePrimaryError = () => {
    // 检测帧不存在/过期 -> 回退原始快照
    if (!triedFallbackRef.current) {
      triedFallbackRef.current = true;
      setPhase('fallback');
    } else {
      setPhase('error');
    }
  };

  const src = phase === 'fallback' ? fallbackSrc : primarySrc;

  return (
    <AppModal
      open={open}
      onCancel={onClose}
      footer={null}
      kind="media"
      size="full"
      centered
      width="min(1200px, calc(100vw - 40px))"
      className="detection-frame-modal"
      title={
        <span className="df-title">
          <CameraOutlined />
          最新检测帧{name ? ` · ${name}` : ''}
        </span>
      }
      closeIcon={
        <div className="df-close-btn">
          <CloseOutlined />
        </div>
      }
    >
      <div className="df-container">
        <div className="df-toolbar">
          <div className="df-info">
            {phase === 'detection' && <span className="df-badge df-badge-ok">检测帧</span>}
            {phase === 'fallback' && (
              <span className="df-badge df-badge-warn">暂无检测 · 显示原始画面</span>
            )}
            {phase === 'loading' && <span className="df-badge df-badge-muted">加载中…</span>}
          </div>
          <Tooltip title="立即刷新">
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={refresh}
              className="df-refresh-btn"
            >
              刷新
            </Button>
          </Tooltip>
        </div>

        <div className="df-image-wrapper">
          {phase === 'error' ? (
            <div className="df-error">
              <WarningOutlined className="error-icon" />
              <div className="error-text">无法加载画面</div>
            </div>
          ) : (
            <img
              key={src}
              src={src}
              alt="最新检测帧"
              className={`df-image ${phase === 'loading' ? 'loading' : ''}`}
              style={{ display: phase === 'loading' ? 'none' : 'block' }}
              onLoad={() => {
                if (phase !== 'fallback') setPhase('detection');
                else setPhase('fallback');
              }}
              onError={phase === 'fallback' ? () => setPhase('error') : handlePrimaryError}
            />
          )}
          {phase === 'loading' && (
            <div className="df-loading">
              <div className="loading-spinner" />
              <div className="loading-text">加载中...</div>
            </div>
          )}
        </div>
      </div>
    </AppModal>
  );
};

export default DetectionFrameModal;
