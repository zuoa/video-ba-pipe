import { tr, trf } from '@/i18n/tr';
import { useState, useEffect, useCallback, useRef } from 'react';
import { message, Space } from 'antd';
import Button from '@/components/common/AppButton';
import {
  PlusOutlined,
  CloudDownloadOutlined,
  ScanOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import {
  getVideoSources,
  createVideoSource,
  updateVideoSource,
  deleteVideoSource,
  getSourceHealth,
  getPreviewConfig,
  ensurePreviewPath,
  startVideoSourceNow,
} from '@/services/api';
import { AppModal, PageHeader, useAppConfirm } from '@/components/common';
import SourceForm from './components/SourceForm';
import ImportSourcesModal from './components/ImportSourcesModal';
import OnvifScanModal from './components/OnvifScanModal';
import SourceTable from './components/SourceTable';
import SourceHealthModal from './components/SourceHealthModal';
import DetectionFrameModal from './components/DetectionFrameModal';
import WebRtcPreviewModal from './components/WebRtcPreviewModal';
import './index.css';

export default function VideoSources() {
  const [sources, setSources] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [importVisible, setImportVisible] = useState(false);
  const [onvifVisible, setOnvifVisible] = useState(false);
  const [previewVisible, setPreviewVisible] = useState(false);
  const [livePreviewVisible, setLivePreviewVisible] = useState(false);
  const [editingSource, setEditingSource] = useState<any>(null);
  const [previewSource, setPreviewSource] = useState<any>(null);
  const [livePreviewSource, setLivePreviewSource] = useState<any>(null);
  const [previewConfig, setPreviewConfig] = useState<any>({ webrtc_enabled: false });
  const [refreshingId, setRefreshingId] = useState<number | null>(null);
  const [startingId, setStartingId] = useState<number | null>(null);
  const [healthModalVisible, setHealthModalVisible] = useState(false);
  const [healthDetail, setHealthDetail] = useState<any>(null);
  const [switchDecisionVisible, setSwitchDecisionVisible] = useState(false);
  const switchDecisionRef = useRef<{
    resolve: (value: boolean) => void;
    reject: (reason: symbol) => void;
  } | null>(null);
  const confirmAction = useAppConfirm();

  const streamSwitchCancelled = useRef(Symbol('stream-switch-cancelled'));

  const requestStreamSwitchDecision = () => new Promise<boolean>((resolve, reject) => {
    switchDecisionRef.current = { resolve, reject };
    setSwitchDecisionVisible(true);
  });

  const finishStreamSwitchDecision = (switchImmediately?: boolean) => {
    const decision = switchDecisionRef.current;
    switchDecisionRef.current = null;
    setSwitchDecisionVisible(false);
    if (!decision) return;
    if (switchImmediately === undefined) {
      decision.reject(streamSwitchCancelled.current);
    } else {
      decision.resolve(switchImmediately);
    }
  };

  const loadPreviewConfig = useCallback(async () => {
    try {
      const cfg = await getPreviewConfig();
      setPreviewConfig(cfg || { webrtc_enabled: false });
    } catch (error) {
      // 拉取失败时保持禁用，不弹错误打扰用户
      setPreviewConfig({ webrtc_enabled: false });
    }
  }, []);

  useEffect(() => {
    loadPreviewConfig();
  }, [loadPreviewConfig]);

  const loadSources = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getVideoSources();
      setSources(data || []);
    } catch (error) {
      message.error(tr("加载视频源失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSources();
    const interval = setInterval(loadSources, 5000);
    return () => clearInterval(interval);
  }, [loadSources]);

  const handleCreate = () => {
    setEditingSource(null);
    setModalVisible(true);
  };

  const handleOpenImport = () => {
    setImportVisible(true);
  };

  const handleEdit = (record: any) => {
    setEditingSource(record);
    setModalVisible(true);
  };

  const handleDelete = (id: number) => {
    const source = sources.find((item) => item.id === id);
    confirmAction({
      title: tr("删除视频源"),
      objectName: source?.name || trf("视频源 #__VAR0__", [id]),
      description: tr("删除后，关联的工作流将无法继续读取该视频源。"),
      onConfirm: async () => {
        try {
          await deleteVideoSource(id);
          message.success(tr("视频源删除成功"));
          loadSources();
        } catch (error) {
          message.error(tr("删除失败"));
        }
      },
    });
  };

  const handleSubmit = async (values: any) => {
    try {
      if (editingSource) {
        const streamUrlChanged = String(values.source_url || '') !== String(
          editingSource.source_url || '',
        );
        const switchImmediately = streamUrlChanged
          ? await requestStreamSwitchDecision()
          : undefined;
        const result: any = await updateVideoSource(editingSource.id, {
          ...values,
          ...(streamUrlChanged
            ? { switch_stream_immediately: switchImmediately }
            : {}),
        });
        message.success(result?.message || tr("视频源更新成功"));
      } else {
        await createVideoSource(values);
        message.success(tr("视频源创建成功"));
      }
      setModalVisible(false);
      loadSources();
    } catch (error) {
      if (error === streamSwitchCancelled.current) {
        throw error;
      }
      message.error(editingSource ? tr("更新失败") : tr("创建失败"));
      throw error;
    }
  };

  const handlePreview = (source: any) => {
    setPreviewSource(source);
    setPreviewVisible(true);
  };

  const handleLivePreview = async (source: any) => {
    if (!previewConfig?.webrtc_enabled) {
      message.warning(tr("未启用 WebRTC 实时预览，请在系统配置中开启 MediaMTX"));
      return;
    }
    // 懒注册兜底：确保 MediaMTX 已有该源的按需拉流路径
    try {
      await ensurePreviewPath(source.id);
      // 注册成功后再挂载播放器，避免 WHEP 与路径创建并发导致首次请求 404。
      setLivePreviewSource(source);
      setLivePreviewVisible(true);
    } catch (error) {
      message.error(tr("实时预览路径注册失败，请检查 MediaMTX 服务与视频源配置"));
    }
  };

  const handleRefreshStatus = async (source: any) => {
    setRefreshingId(source.id);
    try {
      const detail = await getSourceHealth(source.id);
      setHealthDetail({ ...detail, _name: source.name });
      setHealthModalVisible(true);
      // 探测同时顺手刷新列表里的 DB status
      loadSources();
    } catch (error) {
      const detail = (error as any)?.response?.data?.error;
      message.error(detail || tr("探测状态失败"));
    } finally {
      setRefreshingId(null);
    }
  };

  const handleRetryHealth = () => {
    const sourceId = healthDetail?.source_id;
    if (!sourceId) return;
    const source = sources.find((item) => item.id === sourceId) || healthDetail;
    handleRefreshStatus(source);
  };

  const handleStartNow = async (source: any) => {
    setStartingId(source.id);
    try {
      const result = await startVideoSourceNow(source.id);
      message.success(result?.message || tr("已加入优先启动队列"));
      await loadSources();
    } catch (error) {
      const detail = (error as any)?.response?.data?.error;
      message.error(detail || tr("启动请求失败"));
    } finally {
      setStartingId(null);
    }
  };

  return (
    <div className="video-sources-page">
      <PageHeader
        icon={<VideoCameraOutlined />}
        eyebrow="VIDEO INPUTS"
        title={tr("视频源管理")}
        subtitle={tr("接入、预览并维护视频分析通道")}
        count={sources.length}
        countLabel={tr("个视频源")}
        extra={
          <Space size={12} wrap>
            <Button
              icon={<ScanOutlined />}
              onClick={() => setOnvifVisible(true)}
              size="large"
              className="app-secondary-button import-btn"
            >
              {tr("ONVIF 扫描")}
            </Button>
            <Button
              icon={<CloudDownloadOutlined />}
              onClick={handleOpenImport}
              size="large"
              className="app-secondary-button import-btn"
            >
              {tr("批量导入")}
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={handleCreate}
              size="large"
              className="app-primary-button create-btn"
            >
              {tr("手工添加")}
            </Button>
          </Space>
        }
      />

      <SourceTable
        sources={sources}
        loading={loading}
        onEdit={handleEdit}
        onDelete={handleDelete}
        onPreview={handlePreview}
        onLivePreview={handleLivePreview}
        webrtcEnabled={!!previewConfig?.webrtc_enabled}
        onRefreshStatus={handleRefreshStatus}
        refreshingId={refreshingId}
        onStartNow={handleStartNow}
        startingId={startingId}
      />

      <SourceForm
        visible={modalVisible}
        editingSource={editingSource}
        onCancel={() => setModalVisible(false)}
        onSubmit={handleSubmit}
      />

      <AppModal
        open={switchDecisionVisible}
        title={tr("流地址已变化")}
        description={tr("请选择视频源何时使用新地址")}
        size="sm"
        onCancel={() => finishStreamSwitchDecision()}
        maskClosable={false}
        footer={[
          <Button
            key="back"
            onClick={() => finishStreamSwitchDecision()}
          >
            {tr("返回修改")}
          </Button>,
          <Button
            key="deferred"
            onClick={() => finishStreamSwitchDecision(false)}
          >
            {tr("当前流失效后切换")}
          </Button>,
          <Button
            key="immediate"
            type="primary"
            onClick={() => finishStreamSwitchDecision(true)}
          >
            {tr("立即切换")}
          </Button>,
        ]}
      >
        <p style={{ marginBottom: 8 }}>
          {tr("立即切换会让运行中的解码进程重启并读取新地址；视频源未运行时，下次启动会直接使用新地址。")}
        </p>
        <p style={{ marginBottom: 0, color: 'var(--text-secondary, #667085)' }}>
          {tr("选择“当前流失效后切换”后，当前地址有效时继续使用；发生断流、无帧或接流失败时再启用新地址。")}
        </p>
      </AppModal>

      <ImportSourcesModal
        visible={importVisible}
        onCancel={() => setImportVisible(false)}
        onImported={loadSources}
      />

      <OnvifScanModal
        visible={onvifVisible}
        onCancel={() => setOnvifVisible(false)}
        onImported={loadSources}
      />

      <DetectionFrameModal
        open={previewVisible}
        sourceCode={previewSource?.source_code}
        name={previewSource?.name}
        onClose={() => setPreviewVisible(false)}
      />

      <WebRtcPreviewModal
        open={livePreviewVisible}
        source={livePreviewSource}
        previewConfig={previewConfig}
        onClose={() => setLivePreviewVisible(false)}
      />

      <SourceHealthModal
        open={healthModalVisible}
        detail={healthDetail}
        onClose={() => setHealthModalVisible(false)}
        onRetry={handleRetryHealth}
        retrying={refreshingId === healthDetail?.source_id}
      />
    </div>
  );
}
