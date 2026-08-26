import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { message } from 'antd';
import { BellOutlined, FolderOpenOutlined } from '@ant-design/icons';
import { history } from '@umijs/max';
import { createAlertExport, getAlerts, getAlertTypes, getApiErrorMessage, getVideoSources, getWorkflows } from '@/services/api';
import { isUnauthorizedError } from '@/utils/auth';
import { PageHeader, useAppConfirm } from '@/components/common';
import Button from '@/components/common/AppButton';
import { Alert, Task, Workflow, AlertFilter } from './types';
import { buildAlertQueryParams } from './query';
import AlertCard from './components/AlertCard';
import AlertDetailModal from './components/AlertDetailModal';
import PaginationBar from './components/PaginationBar';
import FilterBar from './components/FilterBar';
import EmptyState from './components/EmptyState';
import './index.css';

const AlertsPage: React.FC = () => {
  // 数据状态
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [alertTypes, setAlertTypes] = useState<string[]>([]);

  // 加载状态
  const [loading, setLoading] = useState(false);

  // 分页状态
  const [pagination, setPagination] = useState({
    page: 1,
    per_page: 20,
    total: 0,
  });

  // 筛选状态
  const [filter, setFilter] = useState<AlertFilter>({});
  const [customTimeRange, setCustomTimeRange] = useState<{ start: string; end: string } | undefined>();

  // 详情模态框状态
  const [detailVisible, setDetailVisible] = useState(false);
  const [selectedAlertIndex, setSelectedAlertIndex] = useState(0);
  const [exporting, setExporting] = useState(false);
  const confirmAction = useAppConfirm();

  // 加载任务列表
  const loadTasks = useCallback(async () => {
    try {
      const data = await getVideoSources();
      setTasks(data || []);
    } catch (error: any) {
      if (!isUnauthorizedError(error)) {
        message.error('加载任务列表失败: ' + error.message);
      }
    }
  }, []);

  // 加载工作流列表
  const loadWorkflows = useCallback(async () => {
    try {
      const data = await getWorkflows();
      setWorkflows(data || []);
    } catch (error: any) {
      if (!isUnauthorizedError(error)) {
        message.error('加载工作流列表失败: ' + error.message);
      }
    }
  }, []);

  // 加载告警类型
  const loadAlertTypes = useCallback(async () => {
    try {
      const types = await getAlertTypes();
      setAlertTypes(types || []);
    } catch (error: any) {
      if (!isUnauthorizedError(error)) {
        message.error('加载告警类型失败: ' + error.message);
      }
    }
  }, []);

  // 加载告警列表
  const loadAlerts = useCallback(async () => {
    setLoading(true);
    try {
      const params: AlertFilter = {
        page: pagination.page,
        per_page: pagination.per_page,
        ...buildAlertQueryParams(filter, customTimeRange),
      };

      const response = await getAlerts(params);
      setAlerts(response.data || []);
      setPagination(prev => ({
        ...prev,
        total: response.pagination?.total || 0,
        page: response.pagination?.page || 1,
        per_page: response.pagination?.per_page || 20,
      }));
    } catch (error: any) {
      if (!isUnauthorizedError(error)) {
        message.error('加载告警列表失败: ' + error.message);
      }
      setAlerts([]);
    } finally {
      setLoading(false);
    }
  }, [pagination.page, pagination.per_page, filter, customTimeRange]);

  // 初始化
  useEffect(() => {
    loadTasks();
    loadWorkflows();
    loadAlertTypes();
  }, [loadTasks, loadWorkflows, loadAlertTypes]);

  // 当筛选或分页变化时重新加载
  useEffect(() => {
    loadAlerts();
  }, [loadAlerts]);

  // 自动刷新（每30秒）
  useEffect(() => {
    const timer = setInterval(() => {
      loadAlerts();
    }, 30000);

    return () => clearInterval(timer);
  }, [loadAlerts]);

  // 处理任务筛选
  const handleTaskChange = (value: string) => {
    setFilter(prev => ({ ...prev, task_id: value || undefined }));
    setPagination(prev => ({ ...prev, page: 1 }));
  };

  // 处理工作流筛选
  const handleWorkflowChange = (value: string) => {
    setFilter(prev => ({ ...prev, workflow_id: value || undefined }));
    setPagination(prev => ({ ...prev, page: 1 }));
  };

  // 处理编排模板筛选
  const handleSourceTemplateChange = (value: string) => {
    setFilter(prev => ({ ...prev, source_template_id: value || undefined }));
    setPagination(prev => ({ ...prev, page: 1 }));
  };

  // 处理告警类型筛选
  const handleAlertTypeChange = (value: string) => {
    setFilter(prev => ({ ...prev, alert_type: value || undefined }));
    setPagination(prev => ({ ...prev, page: 1 }));
  };

  // 处理时间范围筛选
  const handleTimeRangeChange = (value: string, customRange?: { start: string; end: string }) => {
    setFilter(prev => ({ ...prev, time_range: value || undefined }));
    setCustomTimeRange(customRange);
    setPagination(prev => ({ ...prev, page: 1 }));
  };

  // 处理分页变化
  const handlePageChange = (page: number, pageSize?: number) => {
    setPagination(prev => ({
      ...prev,
      page,
      per_page: pageSize || prev.per_page,
    }));
  };

  // 显示详情
  const showDetail = (alertId: number) => {
    const index = alerts.findIndex(a => a.id === alertId);
    if (index !== -1) {
      setSelectedAlertIndex(index);
      setDetailVisible(true);
    }
  };

  // 导航详情
  const handleNavigate = (direction: 'prev' | 'next') => {
    if (direction === 'prev' && selectedAlertIndex > 0) {
      setSelectedAlertIndex(selectedAlertIndex - 1);
    } else if (direction === 'next' && selectedAlertIndex < alerts.length - 1) {
      setSelectedAlertIndex(selectedAlertIndex + 1);
    }
  };

  const selectedAlert = alerts[selectedAlertIndex];
  const tasksById = useMemo(
    () => new Map(tasks.map(task => [task.id, task])),
    [tasks],
  );
  const runtimeWorkflows = useMemo(
    () => workflows.filter(workflow => !workflow.is_template),
    [workflows],
  );
  const workflowTemplates = useMemo(
    () => workflows.filter(workflow => workflow.is_template),
    [workflows],
  );
  const hasActiveFilters = Boolean(
    filter.task_id
    || filter.workflow_id
    || filter.source_template_id
    || filter.alert_type
    || filter.time_range,
  );

  const goToExports = () => history.push('/alerts/exports');

  const handleExport = () => {
    if (!pagination.total) {
      message.warning('当前筛选条件下没有可导出的告警记录');
      return;
    }

    confirmAction({
      tone: 'info',
      title: '导出告警记录',
      objectName: `${pagination.total} 条告警`,
      description: '将按当前筛选导出 CSV 以及标注图、原图，打包为 ZIP，任务在后台执行。',
      confirmText: '开始导出',
      onConfirm: async () => {
        setExporting(true);
        try {
          await createAlertExport(buildAlertQueryParams(filter, customTimeRange));
          message.success({
            content: (
              <span>
                导出进行中，可到
                <Button type="link" onClick={goToExports} style={{ padding: '0 4px' }}>
                  导出管理
                </Button>
                查看进度
              </span>
            ),
            duration: 5,
          });
        } catch (error: any) {
          if (isUnauthorizedError(error)) {
            return;
          }
          const apiMessage = getApiErrorMessage(error, '创建导出任务失败');
          message.error(apiMessage);
          const status = error?.response?.status ?? error?.status;
          if (status === 409 || apiMessage.includes('正在进行')) {
            goToExports();
          }
        } finally {
          setExporting(false);
        }
      },
    });
  };

  return (
    <div className="alerts-page">
      <PageHeader
        icon={<BellOutlined />}
        eyebrow="EVENT LOG"
        title="告警记录"
        subtitle="筛选、回溯并处置视频分析事件"
        count={pagination.total}
        countLabel="条告警"
        extra={(
          <Button icon={<FolderOpenOutlined />} onClick={goToExports}>
            导出管理
          </Button>
        )}
      />

      {/* 筛选栏 */}
      <FilterBar
        tasks={tasks}
        workflows={runtimeWorkflows}
        workflowTemplates={workflowTemplates}
        alertTypes={alertTypes}
        selectedTask={filter.task_id}
        selectedWorkflow={filter.workflow_id}
        selectedSourceTemplate={filter.source_template_id}
        selectedAlertType={filter.alert_type}
        selectedTimeRange={filter.time_range}
        customTimeRange={customTimeRange}
        onTaskChange={handleTaskChange}
        onWorkflowChange={handleWorkflowChange}
        onSourceTemplateChange={handleSourceTemplateChange}
        onAlertTypeChange={handleAlertTypeChange}
        onTimeRangeChange={handleTimeRangeChange}
        onRefresh={loadAlerts}
        onExport={handleExport}
        loading={loading}
        exporting={exporting}
        exportDisabled={pagination.total === 0}
      />

      {/* 顶部分页 */}
      {alerts.length > 0 && (
        <PaginationBar
          current={pagination.page}
          pageSize={pagination.per_page}
          total={pagination.total}
          onChange={handlePageChange}
          position="top"
        />
      )}

      {/* 告警卡片网格 */}
      {alerts.length === 0 ? (
        <EmptyState
          type={hasActiveFilters ? 'search' : 'alerts'}
          onRefresh={loadAlerts}
        />
      ) : (
        <div className="alerts-grid">
          {alerts.map(alert => (
            <div key={alert.id} className="alerts-grid__item">
              <AlertCard
                alert={alert}
                task={tasksById.get(alert.task_id)}
                onClick={() => showDetail(alert.id)}
              />
            </div>
          ))}
        </div>
      )}

      {/* 底部分页 */}
      {alerts.length > 0 && (
        <PaginationBar
          current={pagination.page}
          pageSize={pagination.per_page}
          total={pagination.total}
          onChange={handlePageChange}
          position="bottom"
        />
      )}

      {/* 详情模态框 */}
      <AlertDetailModal
        visible={detailVisible}
        alert={selectedAlert || null}
        tasks={tasks}
        currentIndex={selectedAlertIndex}
        total={alerts.length}
        onClose={() => setDetailVisible(false)}
        onNavigate={handleNavigate}
      />
    </div>
  );
};

export default AlertsPage;
