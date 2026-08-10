import { useState, useEffect, useCallback } from 'react';
import { message, Space } from 'antd';
import Button from '@/components/common/AppButton';
import {
  PlusOutlined,
  CloudDownloadOutlined,
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
} from '@/services/api';
import { PageHeader, useAppConfirm } from '@/components/common';
import SourceForm from './components/SourceForm';
import ImportSourcesModal from './components/ImportSourcesModal';
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
  const [previewVisible, setPreviewVisible] = useState(false);
  const [livePreviewVisible, setLivePreviewVisible] = useState(false);
  const [editingSource, setEditingSource] = useState<any>(null);
  const [previewSource, setPreviewSource] = useState<any>(null);
  const [livePreviewSource, setLivePreviewSource] = useState<any>(null);
  const [previewConfig, setPreviewConfig] = useState<any>({ webrtc_enabled: false });
  const [refreshingId, setRefreshingId] = useState<number | null>(null);
  const [healthModalVisible, setHealthModalVisible] = useState(false);
  const [healthDetail, setHealthDetail] = useState<any>(null);
  const confirmAction = useAppConfirm();

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
      message.error('加载视频源失败');
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
      title: '删除视频源',
      objectName: source?.name || `视频源 #${id}`,
      description: '删除后，关联的工作流将无法继续读取该视频源。',
      onConfirm: async () => {
        try {
          await deleteVideoSource(id);
          message.success('视频源删除成功');
          loadSources();
        } catch (error) {
          message.error('删除失败');
        }
      },
    });
  };

  const handleSubmit = async (values: any) => {
    try {
      if (editingSource) {
        await updateVideoSource(editingSource.id, values);
        message.success('视频源更新成功');
      } else {
        await createVideoSource(values);
        message.success('视频源创建成功');
      }
      setModalVisible(false);
      loadSources();
    } catch (error) {
      message.error(editingSource ? '更新失败' : '创建失败');
      throw error;
    }
  };

  const handlePreview = (source: any) => {
    setPreviewSource(source);
    setPreviewVisible(true);
  };

  const handleLivePreview = async (source: any) => {
    if (!previewConfig?.webrtc_enabled) {
      message.warning('未启用 WebRTC 实时预览，请在系统配置中开启 MediaMTX');
      return;
    }
    // 懒注册兜底：确保 MediaMTX 已有该源的按需拉流路径
    try {
      await ensurePreviewPath(source.id);
      // 注册成功后再挂载播放器，避免 WHEP 与路径创建并发导致首次请求 404。
      setLivePreviewSource(source);
      setLivePreviewVisible(true);
    } catch (error) {
      message.error('实时预览路径注册失败，请检查 MediaMTX 服务与视频源配置');
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
      message.error('探测状态失败');
    } finally {
      setRefreshingId(null);
    }
  };

  return (
    <div className="video-sources-page">
      <PageHeader
        icon={<VideoCameraOutlined />}
        title="视频源管理"
        subtitle="管理和配置视频源"
        count={sources.length}
        countLabel="个视频源"
        extra={
          <Space size={12} wrap>
            <Button
              icon={<CloudDownloadOutlined />}
              onClick={handleOpenImport}
              size="large"
              className="app-secondary-button import-btn"
            >
              批量导入
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={handleCreate}
              size="large"
              className="app-primary-button create-btn"
            >
              手工添加
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
      />

      <SourceForm
        visible={modalVisible}
        editingSource={editingSource}
        onCancel={() => setModalVisible(false)}
        onSubmit={handleSubmit}
      />

      <ImportSourcesModal
        visible={importVisible}
        onCancel={() => setImportVisible(false)}
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
      />
    </div>
  );
}
