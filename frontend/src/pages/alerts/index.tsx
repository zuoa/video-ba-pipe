import React, { useState, useEffect, useCallback } from 'react';
import { Row, Col, message, Card, Space, Typography, Statistic } from 'antd';
import {
  BellOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { getAlerts, getAlertTypes, getVideoSources } from '@/services/api';
import { Alert, Task, AlertFilter } from './types';
import AlertCard from './components/AlertCard';
import AlertDetailModal from './components/AlertDetailModal';
import PaginationBar from './components/PaginationBar';
import FilterBar from './components/FilterBar';
import EmptyState from './components/EmptyState';

const { Title, Text } = Typography;

const AlertsPage: React.FC = () => {
  // 数据状态
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
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
  }, [pagination.page, pagination.per_page, filter]);

  // 初始化
  useEffect(() => {
    loadTasks();
    loadAlertTypes();
  }, [loadTasks, loadAlertTypes]);

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

  // 处理告警类型筛选
  const handleAlertTypeChange = (value: string) => {
    setFilter(prev => ({ ...prev, alert_type: value || undefined }));
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
    <div style={{ padding: 24 }}>
      {/* 页面头部 */}
      <Card style={{ marginBottom: 16 }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space>
            <div
              style={{
                width: 48,
                height: 48,
                borderRadius: 12,
                background: 'linear-gradient(135deg, #000000 0%, #333333 100%)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#fff',
                fontSize: 20,
                boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
              }}
            >
              <BellOutlined />
            </div>
            <div>
              <Title level={4} style={{ margin: 0 }}>告警记录</Title>
              <Text type="secondary">实时监控和管理告警信息</Text>
            </div>
          </Space>
          <Statistic title="告警总数" value={pagination.total} />
        </Space>
      </Card>

      {/* 筛选栏 */}
      <FilterBar
        tasks={tasks}
        alertTypes={alertTypes}
        selectedTask={filter.task_id}
        selectedAlertType={filter.alert_type}
        onTaskChange={handleTaskChange}
        onAlertTypeChange={handleAlertTypeChange}
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
