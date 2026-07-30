import { useState, useEffect, useCallback } from 'react';
import { message } from 'antd';
import Button from '@/components/common/AppButton';
import { useNavigate } from '@umijs/max';
import {
  PlusOutlined,
  ExperimentOutlined,
  BulbOutlined,
} from '@ant-design/icons';
import {
  getAlgorithms,
  getPluginModules,
  deleteAlgorithm,
} from '@/services/api';
import { PageHeader, useAppConfirm } from '@/components/common';
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
  const confirmAction = useAppConfirm();

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
      const data = await getPluginModules();
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
    const algorithm = algorithms.find((item) => item.id === id);
    confirmAction({
      title: '删除算法',
      objectName: algorithm?.name || `算法 #${id}`,
      description: '删除后，工作流将无法再选择该算法。',
      onConfirm: async () => {
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
              className="app-primary-button wizard-btn"
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
