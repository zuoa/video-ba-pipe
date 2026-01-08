import { useState, useEffect, useCallback } from 'react';
import { Modal, message, Button } from 'antd';
import { useNavigate } from '@umijs/max';
import {
  PlusOutlined,
  ExperimentOutlined,
  BulbOutlined,
} from '@ant-design/icons';
import {
  getAlgorithms,
  deleteAlgorithm,
} from '@/services/api';
import { PageHeader } from '@/components/common';
import AlgorithmTable from './components/AlgorithmTable';
import TestModal from './components/TestModal';
import type { Algorithm } from './components/AlgorithmTable';
import './index.css';

export default function Algorithms() {
  const navigate = useNavigate();
  const [algorithms, setAlgorithms] = useState<Algorithm[]>([]);
  const [pluginModules, setPluginModules] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [testModalVisible, setTestModalVisible] = useState(false);
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
    navigate('/algorithms/wizard');
  };

  const handleEdit = (algorithm: Algorithm) => {
    navigate(`/algorithms/wizard?edit=${algorithm.id}`);
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


  const handleTest = (algorithm: Algorithm) => {
    setTestingAlgorithm(algorithm);
    setTestModalVisible(true);
  };

  const handleOpenWizard = () => {
    navigate('/algorithms/wizard');
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
              type="primary"
              icon={<BulbOutlined />}
              onClick={handleOpenWizard}
              className="wizard-btn"
            >
              配置向导
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


      <TestModal
        visible={testModalVisible}
        algorithm={testingAlgorithm}
        onCancel={() => setTestModalVisible(false)}
      />
    </div>

  );
}
