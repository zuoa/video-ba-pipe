import React, { useState, useEffect, useRef } from 'react';
import { Button, Tooltip } from 'antd';
import { ReloadOutlined, CloseOutlined, VideoCameraOutlined } from '@ant-design/icons';
import AppModal from '@/components/common/AppModal';
import './WebRtcPreviewModal.css';

export interface WebRtcPreviewModalProps {
  open: boolean;
  source?: any;
  previewConfig?: any;
  onClose: () => void;
}

type Phase = 'connecting' | 'playing' | 'failed';

const ICE_GATHER_TIMEOUT_MS = 2500;

const WebRtcPreviewModal: React.FC<WebRtcPreviewModalProps> = ({
  open,
  source,
  previewConfig,
  onClose,
}) => {
  const [phase, setPhase] = useState<Phase>('connecting');
  const [errorText, setErrorText] = useState<string>('');
  const videoRef = useRef<HTMLVideoElement>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);

  const buildWhepUrl = (): string | null => {
    const code = source?.source_code;
    if (!code) return null;
    const base = (previewConfig?.whep_base_url || '').trim();
    const port = previewConfig?.webrtc_port || 8889;
    const root = base || `${window.location.protocol}//${window.location.hostname}:${port}`;
    return `${root}/${code}/whep`;
  };

  const teardown = () => {
    const pc = pcRef.current;
    pcRef.current = null;
    if (pc) {
      try {
        pc.ontrack = null;
        pc.onconnectionstatechange = null;
        pc.getSenders().forEach((s) => {
          try {
            s.track?.stop();
          } catch {
            /* ignore */
          }
        });
        pc.close();
      } catch {
        /* ignore */
      }
    }
    if (videoRef.current) {
      try {
        const stream = videoRef.current.srcObject as MediaStream | null;
        stream?.getTracks().forEach((t) => t.stop());
        videoRef.current.srcObject = null;
      } catch {
        /* ignore */
      }
    }
  };

  const waitIceComplete = (pc: RTCPeerConnection): Promise<void> => {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        pc.removeEventListener('icegatheringstatechange', check);
        resolve();
      };
      const check = () => {
        if (pc.iceGatheringState === 'complete') finish();
      };
      pc.addEventListener('icegatheringstatechange', check);
      // 兜底：部分环境 ICE gather 不返回 complete，超时后用已收集的候选发送
      setTimeout(finish, ICE_GATHER_TIMEOUT_MS);
    });
  };

  const start = async () => {
    const url = buildWhepUrl();
    if (!url) {
      setPhase('failed');
      setErrorText('缺少视频源标识 (source_code)');
      return;
    }
    if (typeof RTCPeerConnection === 'undefined') {
      setPhase('failed');
      setErrorText('当前浏览器不支持 WebRTC');
      return;
    }

    teardown();
    setPhase('connecting');
    setErrorText('');

    let pc: RTCPeerConnection;
    try {
      pc = new RTCPeerConnection({ iceServers: [] });
      pcRef.current = pc;
    } catch (e: any) {
      setPhase('failed');
      setErrorText('创建 WebRTC 连接失败');
      return;
    }

    pc.ontrack = (event) => {
      if (videoRef.current && event.streams && event.streams[0]) {
        videoRef.current.srcObject = event.streams[0];
        videoRef.current.play().catch(() => {
          /* autoplay 可能被拦截，忽略 */
        });
      }
    };

    pc.onconnectionstatechange = () => {
      const state = pc.connectionState;
      if (state === 'connected') {
        setPhase('playing');
      } else if (state === 'failed' || state === 'disconnected' || state === 'closed') {
        // 关闭由 teardown 触发的 'closed' 不算失败
        if (state !== 'closed') {
          setPhase('failed');
          setErrorText('实时连接中断，请重试');
        }
      }
    };

    try {
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitIceComplete(pc);

      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp' },
        body: pc.localDescription?.sdp,
      });
      if (!resp.ok) {
        throw new Error(`WHEP 信令失败 (HTTP ${resp.status})`);
      }
      const answerSdp = await resp.text();
      await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
      // 进入 connected 由 onconnectionstatechange 设置
    } catch (e: any) {
      if (pcRef.current === pc) {
        setPhase('failed');
        setErrorText(e?.message || '无法建立 WebRTC 连接');
      }
    }
  };

  useEffect(() => {
    if (open) {
      start();
    }
    return () => {
      teardown();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, source?.id, previewConfig?.whep_base_url, previewConfig?.webrtc_port]);

  const titleName = source?.name ? ` · ${source.name}` : '';

  return (
    <AppModal
      open={open}
      onCancel={onClose}
      footer={null}
      kind="media"
      size="full"
      centered
      width="min(1200px, calc(100vw - 40px))"
      className="webrtc-preview-modal"
      title={
        <span className="wr-title">
          <VideoCameraOutlined />
          实时预览{titleName}
          <span className="wr-live-tag">
            <span className="wr-live-dot" /> LIVE
          </span>
        </span>
      }
      closeIcon={
        <div className="wr-close-btn">
          <CloseOutlined />
        </div>
      }
    >
      <div className="wr-container">
        <div className="wr-stage">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className={`wr-video ${phase === 'playing' ? 'visible' : ''}`}
          />

          {phase !== 'playing' && (
            <div className="wr-overlay">
              {phase === 'connecting' && (
                <>
                  <div className="wr-spinner" />
                  <div className="wr-overlay-text">正在建立 WebRTC 连接…</div>
                  <div className="wr-overlay-sub">按需拉流，首次连接可能需要数秒</div>
                </>
              )}
              {phase === 'failed' && (
                <>
                  <div className="wr-overlay-text wr-error-text">实时连接失败</div>
                  <div className="wr-overlay-sub">{errorText || '请检查 MediaMTX 服务与网络配置'}</div>
                  <Button
                    size="small"
                    icon={<ReloadOutlined />}
                    onClick={start}
                    className="wr-retry-btn"
                  >
                    重试
                  </Button>
                </>
              )}
            </div>
          )}
        </div>

        {phase === 'playing' && (
          <div className="wr-footer">
            <span className="wr-hint">WebRTC 实时画面 · 关闭窗口即停止拉流</span>
            <Tooltip title="重新连接">
              <Button
                size="small"
                type="text"
                icon={<ReloadOutlined />}
                onClick={start}
                className="wr-reconnect-btn"
              >
                重连
              </Button>
            </Tooltip>
          </div>
        )}
      </div>
    </AppModal>
  );
};

export default WebRtcPreviewModal;
