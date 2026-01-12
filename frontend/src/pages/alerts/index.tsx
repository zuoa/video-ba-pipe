import React, { useState, useEffect, useCallback } from 'react';
import { Row, Col, message, Card, Space, Typography, Statistic } from 'antd';
import {
  BellOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { getAlerts, getAlertTypes, getVideoSources, getWorkflows } from '@/services/api';
import { PageHeader } from '@/components/common';
import { Alert, Task, Workflow, AlertFilter } from './types';
import AlertCard from './components/AlertCard';
import AlertDetailModal from './components/AlertDetailModal';
import PaginationBar from './components/PaginationBar';
import FilterBar from './components/FilterBar';
import EmptyState from './components/EmptyState';
import './index.css';

const { Title, Text } = Typography;

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

  // 加载任务列表
  const loadTasks = useCallback(async () => {
    try {
      const data = await getVideoSources();
      setTasks(data || []);
    } catch (error: any) {
      message.error('加载任务列表失败: ' + error.message);
    }
  }, []);

  // 加载工作流列表
  const loadWorkflows = useCallback(async () => {
    try {
      const data = await getWorkflows();
      setWorkflows(data || []);
    } catch (error: any) {
      message.error('加载工作流列表失败: ' + error.message);
    }
  }, []);

  // 加载告警类型
  const loadAlertTypes = useCallback(async () => {
    try {
      const types = await getAlertTypes();
      setAlertTypes(types || []);
    } catch (error: any) {
      message.error('加载告警类型失败: ' + error.message);
    }
  }, []);

  // 加载告警列表
  const loadAlerts = useCallback(async () => {
    setLoading(true);
    try {
      const params: AlertFilter = {
        page: pagination.page,
        per_page: pagination.per_page,
        ...filter,
      };

      // 处理时间范围筛选，转换为 start_time 和 end_time
      if (params.time_range && params.time_range !== 'custom') {
        const now = new Date();
        let startTime: Date;

        switch (params.time_range) {
          case '1h':
            startTime = new Date(now.getTime() - 60 * 60 * 1000);
            break;
          case '24h':
            startTime = new Date(now.getTime() - 24 * 60 * 60 * 1000);
            break;
          case '7d':
            startTime = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
            break;
          case '30d':
            startTime = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
            break;
          default:
            startTime = new Date(now.getTime() - 24 * 60 * 60 * 1000);
        }

        params.start_time = startTime.toISOString();
        params.end_time = now.toISOString();

        // 删除 time_range 参数，后端使用 start_time/end_time
        delete params.time_range;
      } else if (params.time_range === 'custom' && customTimeRange) {
        // 使用自定义时间范围
        params.start_time = customTimeRange.start;
        params.end_time = customTimeRange.end;
        delete params.time_range;
      } else {
        // 清除 time_range 参数
        delete params.time_range;
      }

      // 清理空值
      Object.keys(params).forEach(key => {
        if (params[key as keyof AlertFilter] === '' || params[key as keyof AlertFilter] === undefined) {
          delete params[key as keyof AlertFilter];
        }
      });

      const response = await getAlerts(params);
      setAlerts(response.data || []);
      setPagination(prev => ({
        ...prev,
        total: response.pagination?.total || 0,
        page: response.pagination?.page || 1,
        per_page: response.pagination?.per_page || 20,
      }));
    } catch (error: any) {
      message.error('加载告警列表失败: ' + error.message);
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

  return (
    <div className="alerts-page">
      <PageHeader
        icon={<BellOutlined />}
        title="告警记录"
        subtitle="实时监控和管理告警信息"
        count={pagination.total}
        countLabel="条告警"
      />

      {/* 筛选栏 */}
      <FilterBar
        tasks={tasks}
        workflows={workflows}
        alertTypes={alertTypes}
        selectedTask={filter.task_id}
        selectedWorkflow={filter.workflow_id}
        selectedAlertType={filter.alert_type}
        selectedTimeRange={filter.time_range}
        customTimeRange={customTimeRange}
        onTaskChange={handleTaskChange}
        onWorkflowChange={handleWorkflowChange}
        onAlertTypeChange={handleAlertTypeChange}
        onTimeRangeChange={handleTimeRangeChange}
        onRefresh={loadAlerts}
        loading={loading}
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
        <EmptyState type={filter.task_id || filter.alert_type ? 'search' : 'alerts'} onRefresh={loadAlerts} />
      ) : (
        <Row gutter={[16, 16]}>
          {alerts.map(alert => (
            <Col key={alert.id} xs={24} sm={12} lg={8} xl={6}>
              <AlertCard
                alert={alert}
                task={tasks.find(t => t.id === alert.task_id)}
                onClick={() => showDetail(alert.id)}
              />
            </Col>
          ))}
        </Row>
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
