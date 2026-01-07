import { useState, useEffect, useCallback } from 'react';
import { Modal, message, Button } from 'antd';
import {
  PlusOutlined,
  ExperimentOutlined,
  BulbOutlined,
} from '@ant-design/icons';
import {
  getAlgorithms,
  createAlgorithm,
  updateAlgorithm,
  deleteAlgorithm,
} from '@/services/api';
import { PageHeader } from '@/components/common';
import AlgorithmTable from './components/AlgorithmTable';
import AlgorithmForm from './components/AlgorithmForm';
import TestModal from './components/TestModal';
import type { Algorithm } from './components/AlgorithmTable';
import './index.css';

export default function Algorithms() {
  const [algorithms, setAlgorithms] = useState<Algorithm[]>([]);
  const [pluginModules, setPluginModules] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [formVisible, setFormVisible] = useState(false);
  const [testModalVisible, setTestModalVisible] = useState(false);
  const [editingAlgorithm, setEditingAlgorithm] = useState<Algorithm | null>(null);
  const [testingAlgorithm, setTestingAlgorithm] = useState<Algorithm | null>(null);

  const loadAlgorithms = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getAlgorithms();
      setAlgorithms(data || []);
    } catch (error) {
      message.error('加载算法列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPluginModules = useCallback(async () => {
    try {
      const response = await fetch('/api/plugins/modules');
      const data = await response.json();
      setPluginModules(Array.isArray(data.modules) ? data.modules : []);
    } catch (error) {
      console.error('加载插件模块失败:', error);
      setPluginModules(['script_algorithm']);
    }
  }, []);

  useEffect(() => {
    loadAlgorithms();
    loadPluginModules();
  }, [loadAlgorithms, loadPluginModules]);

  const handleCreate = () => {
    setEditingAlgorithm(null);
    setFormVisible(true);
  };

  const handleEdit = (algorithm: Algorithm) => {
    setEditingAlgorithm(algorithm);
    setFormVisible(true);
  };

  const handleDelete = (id: number) => {
    Modal.confirm({
      title: '确认删除',
      content: '确定要删除这个算法吗？',
      okText: '确定',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteAlgorithm(id);
          message.success('算法删除成功');
          loadAlgorithms();
        } catch (error) {
          message.error('删除失败');
        }
      },
    });
  };

  const handleSubmit = async (values: any) => {
    try {
      const data = {
        ...values,
        model_json: JSON.stringify({ models: [] }),
        model_ids: JSON.stringify([]),
        ext_config_json: typeof values.ext_config_json === 'string'
          ? values.ext_config_json
          : JSON.stringify(values.ext_config_json || {}),
      };

      if (editingAlgorithm) {
        await updateAlgorithm(editingAlgorithm.id, data);
        message.success('算法更新成功');
      } else {
        await createAlgorithm(data);
        message.success('算法创建成功');
      }
      setFormVisible(false);
      loadAlgorithms();
    } catch (error) {
      message.error(editingAlgorithm ? '更新失败' : '创建失败');
      throw error;
    }
  };

  const handleTest = (algorithm: Algorithm) => {
    setTestingAlgorithm(algorithm);
    setTestModalVisible(true);
  };

  const handleOpenWizard = () => {
    window.location.href = '/algorithm-wizard';
  };

  return (
    <div className="algorithms-page">
      <PageHeader
        icon={<ExperimentOutlined />}
        title="算法管理"
        subtitle="配置和管理AI算法模型"
        count={algorithms.length}
        countLabel="个算法"
        extra={
          <div className="header-actions">
            <Button
              icon={<BulbOutlined />}
              onClick={handleOpenWizard}
              className="wizard-btn"
            >
              配置向导
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={handleCreate}
              className="create-btn"
            >
              快速添加
            </Button>
          </div>
        }
      />

      <AlgorithmTable
        algorithms={algorithms}
        loading={loading}
        onEdit={handleEdit}
        onDelete={handleDelete}
        onTest={handleTest}
      />

      <AlgorithmForm
        visible={formVisible}
        editingAlgorithm={editingAlgorithm}
        pluginModules={pluginModules}
        onCancel={() => setFormVisible(false)}
        onSubmit={handleSubmit}
      />

      <TestModal
        visible={testModalVisible}
        algorithm={testingAlgorithm}
        onCancel={() => setTestModalVisible(false)}
      />
    </div>
  );
}
