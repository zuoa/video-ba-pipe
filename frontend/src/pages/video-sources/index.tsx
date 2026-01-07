import { useState, useEffect, useCallback } from 'react';
import { Modal, message, Button } from 'antd';
import {
  PlusOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import {
  getVideoSources,
  createVideoSource,
  updateVideoSource,
  deleteVideoSource,
} from '@/services/api';
import { PageHeader, ImagePreview } from '@/components/common';
import SourceForm from './components/SourceForm';
import SourceTable from './components/SourceTable';
import './index.css';

export default function VideoSources() {
  const [sources, setSources] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [previewVisible, setPreviewVisible] = useState(false);
  const [editingSource, setEditingSource] = useState<any>(null);
  const [previewSource, setPreviewSource] = useState<any>(null);

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

  const handleEdit = (record: any) => {
    setEditingSource(record);
    setModalVisible(true);
  };

  const handleDelete = (id: number) => {
    Modal.confirm({
      title: '确认删除',
      content: '确定要删除这个视频源吗？',
      okText: '确定',
      cancelText: '取消',
      onOk: async () => {
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

  return (
    <div className="video-sources-page">
      <PageHeader
        icon={<VideoCameraOutlined />}
        title="视频源管理"
        subtitle="管理和配置视频源"
        count={sources.length}
        countLabel="个视频源"
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleCreate}
            size="large"
            className="create-btn"
          >
            添加视频源
          </Button>
        }
      />

      <SourceTable
        sources={sources}
        loading={loading}
        onEdit={handleEdit}
        onDelete={handleDelete}
        onPreview={handlePreview}
      />

      <SourceForm
        visible={modalVisible}
        editingSource={editingSource}
        onCancel={() => setModalVisible(false)}
        onSubmit={handleSubmit}
      />

      <ImagePreview
        visible={previewVisible}
        src={`/api/image/snapshots/${previewSource?.id}.jpg`}
        title={previewSource?.name}
        onClose={() => setPreviewVisible(false)}
      />
    </div>
  );
}
