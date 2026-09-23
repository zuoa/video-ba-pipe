import { tr, trf } from '@/i18n/tr';
import { lazy, Suspense, useState, useEffect, useCallback } from 'react';
import { message, Space, Spin } from 'antd';
import { useModel } from '@umijs/max';
import { useNavigate } from 'umi';
import Button from '@/components/common/AppButton';
import {
  PlusOutlined,
  ApartmentOutlined,
  ImportOutlined,
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

const TemplateTransferModal = lazy(() => import('./components/TemplateTransferModal'));

export default function Workflows() {
  const navigate = useNavigate();
  const { initialState } = useModel('@@initialState');
  const isAdmin = initialState?.currentUser?.role === 'admin';
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(false);
  const [formVisible, setFormVisible] = useState(false);
  const [copyModalVisible, setCopyModalVisible] = useState(false);
  const [editingWorkflow, setEditingWorkflow] = useState<Workflow | null>(null);
  const [copyingWorkflow, setCopyingWorkflow] = useState<Workflow | null>(null);
  const [videoSources, setVideoSources] = useState<VideoSource[]>([]);
  const [batchConfigWorkflows, setBatchConfigWorkflows] = useState<Workflow[]>([]);
  const [templateTransfer, setTemplateTransfer] = useState<{
    mode: 'export' | 'import';
    template?: Workflow | null;
  } | null>(null);
  const confirmAction = useAppConfirm();

  const loadWorkflows = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getWorkflows();
      setWorkflows(data || []);
    } catch (error) {
      message.error(tr("加载工作流失败"));
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
      title: workflow?.is_template ? tr("删除编排模板") : tr("删除运行编排"),
      objectName: workflow?.name || trf("工作流 #__VAR0__", [id]),
      description: workflow?.is_template
        ? tr("删除后，模板结构将无法恢复；已由该模板生成运行编排时，系统会阻止删除。")
        : tr("删除后，当前编排和节点配置将无法恢复。"),
      onConfirm: async () => {
        try {
          await deleteWorkflow(id);
          message.success(tr("工作流删除成功"));
          loadWorkflows();
        } catch (error: any) {
          message.error(error?.data?.error || error?.message || tr("删除失败"));
        }
      },
    });
  };

  const handleActivate = async (id: number) => {
    try {
      await activateWorkflow(id);
      message.success(tr("激活成功"));
      loadWorkflows();
    } catch (error) {
      message.error(tr("激活失败"));
    }
  };

  const handleDeactivate = async (id: number) => {
    try {
      await deactivateWorkflow(id);
      message.success(tr("停用成功"));
      loadWorkflows();
    } catch (error) {
      message.error(tr("停用失败"));
    }
  };

  const handleSubmit = async (values: WorkflowFormValues) => {
    try {
      if (editingWorkflow) {
        await updateWorkflow(editingWorkflow.id, values);
        message.success(tr("工作流更新成功"));
        setFormVisible(false);
        await loadWorkflows();
      } else {
        const created = await createWorkflow(values);
        message.success(tr("工作流创建成功"));
        setFormVisible(false);
        navigate(`/workflows/editor/${created.id}`);
      }
    } catch (error) {
      message.error(editingWorkflow ? tr("更新失败") : tr("创建失败"));
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
      message.success(trf("已激活 __VAR0__ 个编排__VAR1__", [result?.activated || 0, failedCount ? trf('，__VAR0__ 个失败', [failedCount]) : '']));
      await loadWorkflows();
    } catch (error: any) {
      message.error(error?.data?.error || error?.message || tr("批量激活失败"));
    }
  };

  const handleBatchDeactivate = async (ids: number[]) => {
    try {
      const result: any = await batchDeactivateWorkflows(ids);
      const failedCount = result?.failed?.length || 0;
      message.success(trf("已停用 __VAR0__ 个编排__VAR1__", [result?.deactivated || 0, failedCount ? trf('，__VAR0__ 个失败', [failedCount]) : '']));
      await loadWorkflows();
    } catch (error: any) {
      message.error(error?.data?.error || error?.message || tr("批量停用失败"));
    }
  };

  const handleBatchDelete = (ids: number[]) => {
    confirmAction({
      title: tr("批量删除编排"),
      objectName: trf("__VAR0__ 个算法编排", [ids.length]),
      description: tr("删除后，所选编排和节点配置将无法恢复。"),
      onConfirm: async () => {
        try {
          const result: any = await batchDeleteWorkflows(ids);
          const failedCount = result?.failed?.length || 0;
          message.success(trf("已删除 __VAR0__ 个编排__VAR1__", [result?.deleted || 0, failedCount ? trf('，__VAR0__ 个失败', [failedCount]) : '']));
          await loadWorkflows();
        } catch (error: any) {
          message.error(error?.data?.error || error?.message || tr("批量删除失败"));
          throw error;
        }
      },
    });
  };

  const handleCopyConfirm = async (sourceIds: number[], activateAfterCreation: boolean) => {
    if (!copyingWorkflow) return;
    try {
      const result = await batchCopyWorkflow(
        copyingWorkflow.id,
        sourceIds,
        activateAfterCreation,
      );

      const { errors, summary } = result;

      if (summary && summary.success > 0) {
        message.success(
          trf("成功创建__VAR0__ __VAR1__ 个编排__VAR2__", [activateAfterCreation ? tr("并激活") : '', summary.success, summary.failed > 0 ? trf('，__VAR0__ 个失败', [summary.failed]) : ''])
        );
        loadWorkflows();
      }

      if (errors && errors.length > 0) {
        console.error('部分复制失败:', errors);
      }

      setCopyModalVisible(false);
      setCopyingWorkflow(null);
    } catch (error: any) {
      message.error(error?.data?.error || error?.message || tr("复制失败"));
      throw error;
    }
  };

  return (
    <div className="workflows-page">
      <PageHeader
        icon={<ApartmentOutlined />}
        eyebrow="PIPELINE ORCHESTRATION"
        title={tr("算法编排管理")}
        subtitle={tr("分别管理可复用模板与绑定视频源的运行编排")}
        count={workflows.length}
        countLabel={tr("个算法编排")}
        extra={(
          <Space wrap>
            {isAdmin ? (
              <Button
                icon={<ImportOutlined />}
                onClick={() => setTemplateTransfer({ mode: 'import' })}
                size="large"
              >
                {tr("导入模板")}
              </Button>
            ) : null}
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={handleCreate}
              size="large"
              className="app-primary-button create-btn"
            >
              {tr("新建算法编排")}
            </Button>
          </Space>
        )}
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
        onExport={isAdmin
          ? (template) => setTemplateTransfer({ mode: 'export', template })
          : undefined}
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

      {templateTransfer ? (
        <Suspense fallback={<div className="template-transfer-loading"><Spin /></div>}>
          <TemplateTransferModal
            open
            mode={templateTransfer.mode}
            template={templateTransfer.template}
            onClose={() => setTemplateTransfer(null)}
            onImported={loadWorkflows}
          />
        </Suspense>
      ) : null}

    </div>
  );
}
