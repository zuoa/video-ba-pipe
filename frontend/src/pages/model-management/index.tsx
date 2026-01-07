import React, { useState, useEffect, useCallback } from 'react';
import { Row, Col, message, Spin } from 'antd';
import {
  PlusOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import { getModels, getModelTypes, getModelFrameworks } from '@/services/api';
import { PageHeader } from '@/components/common';
import ModelCard from './components/ModelCard';
import FilterBar from './components/FilterBar';
import UploadModal from './components/UploadModal';
import DetailModal from './components/DetailModal';
import EmptyState from './components/EmptyState';
import './index.css';

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

interface ModelFilter {
  search?: string;
  type?: string;
  framework?: string;
  enabledOnly?: boolean;
}

const ModelsPage: React.FC = () => {
  const [models, setModels] = useState<Model[]>([]);
  const [filteredModels, setFilteredModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadModalVisible, setUploadModalVisible] = useState(false);
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [selectedModel, setSelectedModel] = useState<Model | null>(null);

  const [filter, setFilter] = useState<ModelFilter>({});
  const [modelTypes, setModelTypes] = useState<string[]>([]);
  const [modelFrameworks, setModelFrameworks] = useState<string[]>([]);

  // 加载模型列表
  const loadModels = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getModels();
      setModels(data.models || []);
    } catch (error: any) {
      message.error('加载模型列表失败: ' + error.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // 加载筛选器选项
  const loadFilterOptions = useCallback(async () => {
    try {
      const [typesData, frameworksData] = await Promise.all([
        getModelTypes(),
        getModelFrameworks(),
      ]);
      setModelTypes(typesData.types || []);
      setModelFrameworks(frameworksData.frameworks || []);
    } catch (error: any) {
      console.error('加载筛选器选项失败:', error);
    }
  }, []);

  useEffect(() => {
    loadModels();
    loadFilterOptions();
  }, [loadModels, loadFilterOptions]);

  // 筛选模型
  useEffect(() => {
    let filtered = [...models];

    if (filter.search) {
      const searchLower = filter.search.toLowerCase();
      filtered = filtered.filter(
        (m) =>
          m.name.toLowerCase().includes(searchLower) ||
          (m.description && m.description.toLowerCase().includes(searchLower))
      );
    }

    if (filter.type) {
      filtered = filtered.filter((m) => m.model_type === filter.type);
    }

    if (filter.framework) {
      filtered = filtered.filter((m) => m.framework === filter.framework);
    }

    if (filter.enabledOnly) {
      filtered = filtered.filter((m) => m.enabled);
    }

    setFilteredModels(filtered);
  }, [models, filter]);

  // 处理筛选器变化
  const handleFilterChange = (newFilter: Partial<ModelFilter>) => {
    setFilter((prev) => ({ ...prev, ...newFilter }));
  };

  // 处理上传
  const handleUpload = () => {
    setUploadModalVisible(false);
    loadModels();
    loadFilterOptions();
  };

  // 处理删除
  const handleDelete = async (id: number) => {
    try {
      message.success('模型删除成功');
      loadModels();
      loadFilterOptions();
    } catch (error: any) {
      message.error('删除失败: ' + error.message);
    }
  };

  // 显示详情
  const showDetail = (model: Model) => {
    setSelectedModel(model);
    setDetailModalVisible(true);
  };

  return (
    <div className="models-page">
      <PageHeader
        icon={<ApiOutlined />}
        title="模型管理"
        subtitle="管理和上传AI模型文件"
        count={filteredModels.length}
        countLabel="个模型"
        extra={
          <button
            type="button"
            className="upload-btn"
            onClick={() => setUploadModalVisible(true)}
          >
            <PlusOutlined />
            <span>上传模型</span>
          </button>
        }
      />

      <FilterBar
        modelTypes={modelTypes}
        modelFrameworks={modelFrameworks}
        filter={filter}
        onFilterChange={handleFilterChange}
      />

      {loading ? (
        <div className="loading-container">
          <Spin size="large" />
        </div>
      ) : filteredModels.length === 0 ? (
        <EmptyState
          hasFilter={!!(filter.search || filter.type || filter.framework || filter.enabledOnly)}
          onReset={() => setFilter({})}
        />
      ) : (
        <Row gutter={[16, 16]} className="models-grid">
          {filteredModels.map((model) => (
            <Col key={model.id} xs={24} sm={12} lg={8} xl={6}>
              <ModelCard
                model={model}
                onView={showDetail}
                onDelete={handleDelete}
              />
            </Col>
          ))}
        </Row>
      )}

      <UploadModal
        visible={uploadModalVisible}
        onCancel={() => setUploadModalVisible(false)}
        onSuccess={handleUpload}
      />

      <DetailModal
        visible={detailModalVisible}
        model={selectedModel}
        onClose={() => setDetailModalVisible(false)}
      />
    </div>
  );
};

export default ModelsPage;
