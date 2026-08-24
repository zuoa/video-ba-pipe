import { useEffect, useState } from 'react';
import { Row, Col } from 'antd';
import {
  AlertOutlined,
  ApartmentOutlined,
  ExperimentOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import {
  getVideoSources,
  getWorkflows,
  getAlgorithms,
  getModels,
  getTodayAlertsCount,
  getAlerts,
  getSystemMetrics,
} from '@/services/api';
import StatCard from './components/StatCard';
import ChannelAlertChart from './components/ChannelAlertChart';
import AlertTrendChart from './components/AlertTrendChart';
import LatestAlertTicker from './components/LatestAlertTicker';
import SystemMonitor from './components/SystemMonitor';
import type { SystemMetrics } from './components/SystemMonitor';
import type {
  TickerAlert as AlertType,
  TickerTask as TaskType,
} from './components/LatestAlertTicker';
import './index.css';

export default function Dashboard() {
  const [stats, setStats] = useState({
    totalSources: 0,
    runningSources: 0,
    totalWorkflows: 0,
    activeWorkflows: 0,
    totalAlgorithms: 0,
    totalModels: 0,
    todayAlerts: 0,
  });
  const [alerts, setAlerts] = useState<AlertType[]>([]);
  const [tasks, setTasks] = useState<TaskType[]>([]);
  const [loading, setLoading] = useState(true);
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics | null>(null);
  const [systemMetricsLoading, setSystemMetricsLoading] = useState(true);
  const [systemMetricsError, setSystemMetricsError] = useState('');

  useEffect(() => {
    loadDashboardData();

    // 自动刷新（每30秒）
    const interval = setInterval(loadDashboardData, 30000);

    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let refreshTimer: ReturnType<typeof setTimeout>;

    const loadSystemMetrics = async () => {
      try {
        const response = await getSystemMetrics();
        if (!cancelled) {
          setSystemMetrics(response?.data || null);
          setSystemMetricsError('');
        }
      } catch (error) {
        console.error('加载系统状态失败:', error);
        if (!cancelled) {
          setSystemMetricsError('无法读取系统指标，请检查服务权限或稍后重试。');
        }
      } finally {
        if (!cancelled) {
          setSystemMetricsLoading(false);
          refreshTimer = setTimeout(loadSystemMetrics, 5000);
        }
      }
    };

    loadSystemMetrics();

    return () => {
      cancelled = true;
      clearTimeout(refreshTimer);
    };
  }, []);

  const loadDashboardData = async () => {
    try {
      setLoading(true);

      // 并行加载数据
      const [
        sources,
        workflows,
        algorithms,
        modelsResponse,
        alertsResponse,
        todayAlertsResponse,
      ] =
        await Promise.all([
          getVideoSources(),
          getWorkflows(),
          getAlgorithms(),
          getModels(),
          getAlerts({ page: 1, per_page: 3 }),
          getTodayAlertsCount(),
        ]);

      const runningSources = sources?.filter(
        (source) => source.status?.toUpperCase() === 'RUNNING',
      ).length || 0;
      const runtimeWorkflows = workflows?.filter((workflow) => !workflow.is_template) || [];
      const totalModels = Array.isArray(modelsResponse?.models)
        ? modelsResponse.models.length
        : Number(modelsResponse?.total) || 0;

      setStats({
        totalSources: sources?.length || 0,
        runningSources,
        totalWorkflows: runtimeWorkflows.length,
        activeWorkflows: runtimeWorkflows.filter((workflow) => workflow.is_active).length,
        totalAlgorithms: algorithms?.length || 0,
        totalModels,
        todayAlerts: todayAlertsResponse?.count || 0,
      });

      setTasks(sources || []);
      setAlerts(alertsResponse?.data || []);
    } catch (error) {
      console.error('加载仪表盘数据失败:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="dashboard-page">
      {/* 资产概览 */}
      <Row gutter={[14, 14]} className="dashboard-stats-row">
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<VideoCameraOutlined />}
            title="视频源"
            value={`${stats.runningSources} / ${stats.totalSources}`}
            subtitle="运行中 / 总数"
            iconBgColor="#14202b"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<ApartmentOutlined />}
            title="工作流"
            value={`${stats.activeWorkflows} / ${stats.totalWorkflows}`}
            subtitle="启用中 / 总数"
            iconBgColor="#203b48"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<ExperimentOutlined />}
            title="算法与模型"
            value={stats.totalAlgorithms + stats.totalModels}
            subtitle={`算法 ${stats.totalAlgorithms} · 模型 ${stats.totalModels}`}
            iconBgColor="#2f5f68"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<AlertOutlined />}
            title="今日告警"
            value={stats.todayAlerts}
            subtitle="今日累计触发"
            iconBgColor="#b54743"
            footer={(
              <LatestAlertTicker
                alerts={alerts}
                tasks={tasks}
                loading={loading}
                maxItems={3}
              />
            )}
          />
        </Col>
      </Row>

      <SystemMonitor
        metrics={systemMetrics}
        loading={systemMetricsLoading}
        error={systemMetricsError}
      />

      {/* 告警趋势和通道告警 */}
      <Row gutter={[14, 14]} className="dashboard-insights-row">
        <Col xs={24} lg={12}>
          <AlertTrendChart />
        </Col>
        <Col xs={24} lg={12}>
          <ChannelAlertChart />
        </Col>
      </Row>
    </div>
  );
}
