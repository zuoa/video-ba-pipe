import { useState, useEffect, useCallback } from 'react';
import { message } from 'antd';
import { useNavigate } from 'umi';
import Button from '@/components/common/AppButton';
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
  batchActivateWorkflows,
  batchDeactivateWorkflows,
  batchDeleteWorkflows,
} from '@/services/api';
import type { VideoSource, Workflow, WorkflowFormValues } from '@/services/api';
import { PageHeader, useAppConfirm } from '@/components/common';
import WorkflowTable from './components/WorkflowTable';
import WorkflowForm from './components/WorkflowForm';
import CopyWorkflowModal from './components/CopyWorkflowModal';
import BatchConfigDrawer from './components/BatchConfigDrawer';
import './index.css';

export default function Workflows() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(false);
  const [formVisible, setFormVisible] = useState(false);
  const [copyModalVisible, setCopyModalVisible] = useState(false);
  const [editingWorkflow, setEditingWorkflow] = useState<Workflow | null>(null);
  const [copyingWorkflow, setCopyingWorkflow] = useState<Workflow | null>(null);
  const [videoSources, setVideoSources] = useState<VideoSource[]>([]);
  const [batchConfigWorkflows, setBatchConfigWorkflows] = useState<Workflow[]>([]);
  const confirmAction = useAppConfirm();

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

  const handleEdit = (record: Workflow) => {
    setEditingWorkflow(record);
    setFormVisible(true);
  };

  const handleDelete = (id: number) => {
    const workflow = workflows.find((item) => item.id === id);
    confirmAction({
      title: workflow?.is_template ? '删除编排模板' : '删除运行编排',
      objectName: workflow?.name || `工作流 #${id}`,
      description: workflow?.is_template
        ? '删除后，模板结构将无法恢复；已由该模板生成运行编排时，系统会阻止删除。'
        : '删除后，当前编排和节点配置将无法恢复。',
      onConfirm: async () => {
        try {
          await deleteWorkflow(id);
          message.success('工作流删除成功');
          loadWorkflows();
        } catch (error: any) {
          message.error(error?.data?.error || error?.message || '删除失败');
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

  const handleSubmit = async (values: WorkflowFormValues) => {
    try {
      if (editingWorkflow) {
        await updateWorkflow(editingWorkflow.id, values);
        message.success('工作流更新成功');
        setFormVisible(false);
        await loadWorkflows();
      } else {
        const created = await createWorkflow(values);
        message.success('工作流创建成功');
        setFormVisible(false);
        navigate(`/workflows/editor/${created.id}`);
      }
    } catch (error) {
      message.error(editingWorkflow ? '更新失败' : '创建失败');
      throw error;
    }
  };

  const handleCopy = (workflow: Workflow) => {
    setCopyingWorkflow(workflow);
    setCopyModalVisible(true);
  };

  const handleBatchActivate = async (ids: number[]) => {
    try {
      const result: any = await batchActivateWorkflows(ids);
      const failedCount = result?.failed?.length || 0;
      message.success(`已激活 ${result?.activated || 0} 个编排${failedCount ? `，${failedCount} 个失败` : ''}`);
      await loadWorkflows();
    } catch (error: any) {
      message.error(error?.data?.error || error?.message || '批量激活失败');
    }
  };

  const handleBatchDeactivate = async (ids: number[]) => {
    try {
      const result: any = await batchDeactivateWorkflows(ids);
      const failedCount = result?.failed?.length || 0;
      message.success(`已停用 ${result?.deactivated || 0} 个编排${failedCount ? `，${failedCount} 个失败` : ''}`);
      await loadWorkflows();
    } catch (error: any) {
      message.error(error?.data?.error || error?.message || '批量停用失败');
    }
  };

  const handleBatchDelete = (ids: number[]) => {
    confirmAction({
      title: '批量删除编排',
      objectName: `${ids.length} 个算法编排`,
      description: '删除后，所选编排和节点配置将无法恢复。',
      onConfirm: async () => {
        try {
          const result: any = await batchDeleteWorkflows(ids);
          const failedCount = result?.failed?.length || 0;
          message.success(`已删除 ${result?.deleted || 0} 个编排${failedCount ? `，${failedCount} 个失败` : ''}`);
          await loadWorkflows();
        } catch (error: any) {
          message.error(error?.data?.error || error?.message || '批量删除失败');
          throw error;
        }
      },
    });
  };

  const handleCopyConfirm = async (sourceIds: number[]) => {
    if (!copyingWorkflow) return;
    try {
      const result = await batchCopyWorkflow(copyingWorkflow.id, sourceIds);

      const { errors, summary } = result;

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
      message.error(error?.data?.error || error?.message || '复制失败');
      throw error;
    }
  };

  return (
    <div className="workflows-page">
      <PageHeader
        icon={<ApartmentOutlined />}
        title="算法编排管理"
        subtitle="分别管理可复用模板与绑定视频源的运行编排"
        count={workflows.length}
        countLabel="个算法编排"
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleCreate}
            size="large"
            className="app-primary-button create-btn"
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
        onActivate={handleActivate}
        onDeactivate={handleDeactivate}
        onCopy={handleCopy}
        onBatchActivate={handleBatchActivate}
        onBatchDeactivate={handleBatchDeactivate}
        onBatchDelete={handleBatchDelete}
        onBatchConfig={setBatchConfigWorkflows}
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
        workflows={workflows}
        videoSources={videoSources}
        onCopy={handleCopyConfirm}
        onCancel={() => {
          setCopyModalVisible(false);
          setCopyingWorkflow(null);
        }}
      />

      <BatchConfigDrawer
        open={batchConfigWorkflows.length > 0}
        workflows={batchConfigWorkflows}
        onClose={() => setBatchConfigWorkflows([])}
        onApplied={loadWorkflows}
      />

    </div>
  );
}
