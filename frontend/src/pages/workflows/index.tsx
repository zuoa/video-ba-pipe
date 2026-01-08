import { useState, useEffect, useCallback } from 'react';
import { Modal, message, Button } from 'antd';
import {
  PlusOutlined,
  ApartmentOutlined,
} from '@ant-design/icons';
import {
  getWorkflows,
  createWorkflow,
  updateWorkflow,
  deleteWorkflow,
  activateWorkflow,
  deactivateWorkflow,
  getVideoSources,
  batchCopyWorkflow,
} from '@/services/api';
import { PageHeader } from '@/components/common';
import WorkflowTable from './components/WorkflowTable';
import WorkflowForm from './components/WorkflowForm';
import CopyWorkflowModal from './components/CopyWorkflowModal';
import './index.css';

export default function Workflows() {
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [formVisible, setFormVisible] = useState(false);
  const [editorVisible, setEditorVisible] = useState(false);
  const [copyModalVisible, setCopyModalVisible] = useState(false);
  const [editingWorkflow, setEditingWorkflow] = useState<any>(null);
  const [copyingWorkflow, setCopyingWorkflow] = useState<any>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<any>(null);
  const [videoSources, setVideoSources] = useState<any[]>([]);

  const loadWorkflows = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getWorkflows();
      setWorkflows(data || []);
    } catch (error) {
      message.error('加载工作流失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadVideoSources = useCallback(async () => {
    try {
      const data = await getVideoSources();
      setVideoSources(data || []);
    } catch (error) {
      console.error('加载视频源失败:', error);
    }
  }, []);

  useEffect(() => {
    loadWorkflows();
    loadVideoSources();
  }, [loadWorkflows, loadVideoSources]);

  const handleCreate = () => {
    setEditingWorkflow(null);
    setFormVisible(true);
  };

  const handleEdit = (record: any) => {
    setEditingWorkflow(record);
    setFormVisible(true);
  };

  const handleOpenEditor = (record: any) => {
    setSelectedWorkflow(record);
    setEditorVisible(true);
  };

  const handleDelete = (id: number) => {
    Modal.confirm({
      title: '确认删除',
      content: '确定要删除这个工作流吗？',
      okText: '确定',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteWorkflow(id);
          message.success('工作流删除成功');
          loadWorkflows();
        } catch (error) {
          message.error('删除失败');
        }
      },
    });
  };

  const handleActivate = async (id: number) => {
    try {
      await activateWorkflow(id);
      message.success('激活成功');
      loadWorkflows();
    } catch (error) {
      message.error('激活失败');
    }
  };

  const handleDeactivate = async (id: number) => {
    try {
      await deactivateWorkflow(id);
      message.success('停用成功');
      loadWorkflows();
    } catch (error) {
      message.error('停用失败');
    }
  };

  const handleSubmit = async (values: any) => {
    try {
      if (editingWorkflow) {
        await updateWorkflow(editingWorkflow.id, values);
        message.success('工作流更新成功');
      } else {
        await createWorkflow(values);
        message.success('工作流创建成功');
      }
      setFormVisible(false);
      loadWorkflows();
    } catch (error) {
      message.error(editingWorkflow ? '更新失败' : '创建失败');
      throw error;
    }
  };

  const handleEditorSubmit = async (graphData: any) => {
    try {
      // 后端期望的字段是 workflow_data，不是 graph_json
      await updateWorkflow(selectedWorkflow.id, {
        workflow_data: graphData, // 直接传递对象，让前端库自动序列化
      });
      message.success('保存成功');
      loadWorkflows();
    } catch (error) {
      message.error('保存失败');
      throw error;
    }
  };

  const handleCopy = (workflow: any) => {
    setCopyingWorkflow(workflow);
    setCopyModalVisible(true);
  };

  const handleCopyConfirm = async (sourceIds: number[]) => {
    try {
      const result = await batchCopyWorkflow(copyingWorkflow.id, sourceIds);

      const { created, errors, summary } = result as any;

      if (summary && summary.success > 0) {
        message.success(
          `成功复制 ${summary.success} 个编排${summary.failed > 0 ? `，${summary.failed} 个失败` : ''}`
        );
        loadWorkflows();
      }

      if (errors && errors.length > 0) {
        console.error('部分复制失败:', errors);
      }

      setCopyModalVisible(false);
      setCopyingWorkflow(null);
    } catch (error: any) {
      message.error(error.message || '复制失败');
      throw error;
    }
  };

  return (
    <div className="workflows-page">
      <PageHeader
        icon={<ApartmentOutlined />}
        title="算法编排管理"
        subtitle="可视化配置视频分析算法编排"
        count={workflows.length}
        countLabel="个算法编排"
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleCreate}
            size="large"
            className="create-btn"
          >
            新建算法编排
          </Button>
        }
      />

      <WorkflowTable
        workflows={workflows}
        loading={loading}
        videoSources={videoSources}
        onEdit={handleEdit}
        onDelete={handleDelete}
        onOpenEditor={handleOpenEditor}
        onActivate={handleActivate}
        onDeactivate={handleDeactivate}
        onCopy={handleCopy}
      />

      <WorkflowForm
        visible={formVisible}
        editingWorkflow={editingWorkflow}
        onCancel={() => setFormVisible(false)}
        onSubmit={handleSubmit}
      />

      <CopyWorkflowModal
        visible={copyModalVisible}
        workflow={copyingWorkflow}
        videoSources={videoSources}
        onCopy={handleCopyConfirm}
        onCancel={() => {
          setCopyModalVisible(false);
          setCopyingWorkflow(null);
        }}
      />

    </div>
  );
}
