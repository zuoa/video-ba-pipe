import { useEffect, useState } from 'react';
import { Row, Col } from 'antd';
import {
  AppstoreOutlined,
  AlertOutlined,
  HistoryOutlined,
  ArrowUpOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  ExperimentOutlined,
} from '@ant-design/icons';
import {
  getVideoSources,
  getAlgorithms,
  getTodayAlertsCount,
  getAlerts,
  getSystemMetrics,
} from '@/services/api';
import StatCard from './components/StatCard';
import ChannelAlertChart from './components/ChannelAlertChart';
import RecentAlertCard from './components/RecentAlertCard';
import WelcomeBanner from './components/WelcomeBanner';
import SystemMonitor from './components/SystemMonitor';
import type { SystemMetrics } from './components/SystemMonitor';
import type { Alert as AlertType, Task as TaskType } from './components/RecentAlertCard';
import './index.css';

export default function Dashboard() {
  const [stats, setStats] = useState({
    totalTasks: 0,
    runningTasks: 0,
    totalAlgorithms: 0,
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
      const [sources, algorithms, alertsResponse, todayAlertsResponse] =
        await Promise.all([
          getVideoSources(),
          getAlgorithms(),
          getAlerts({ page: 1, per_page: 5 }),
          getTodayAlertsCount(),
        ]);

      const runningTasksCount = sources?.filter((t: any) => t.status === 'RUNNING').length || 0;

      setStats({
        totalTasks: sources?.length || 0,
        runningTasks: runningTasksCount,
        totalAlgorithms: algorithms?.length || 0,
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
      <WelcomeBanner />

      {/* 统计卡片 */}
      <Row gutter={[14, 14]} className="dashboard-stats-row">
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<AppstoreOutlined />}
            title="视频源总数"
            value={stats.totalTasks}
            subtitle={`当前有 ${stats.runningTasks} 路视频源正在运行`}
            iconBgColor="#14202b"
            trendIcon={<ArrowUpOutlined />}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<ExperimentOutlined />}
            title="算法模型"
            value={stats.totalAlgorithms}
            subtitle="已接入的算法与模型总数"
            iconBgColor="#203b48"
            trendIcon={<CheckCircleOutlined />}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<AlertOutlined />}
            title="今日告警"
            value={stats.todayAlerts}
            subtitle="今日累计触发的告警次数"
            iconBgColor="#b54743"
            trendIcon={<ExclamationCircleOutlined />}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<CheckCircleOutlined />}
            title="系统状态"
            value={systemMetricsError && !systemMetrics ? '监控异常' : systemMetrics ? '运行中' : '检测中'}
            subtitle={systemMetrics
              ? `${systemMetrics.hostname} · 已采集主机资源状态`
              : '正在连接系统状态服务'}
            iconBgColor={systemMetricsError && !systemMetrics ? '#9a681f' : '#2f6b4f'}
            trendIcon={systemMetricsError && !systemMetrics
              ? <ExclamationCircleOutlined />
              : <CheckCircleOutlined />}
          />
        </Col>
      </Row>

      <SystemMonitor
        metrics={systemMetrics}
        loading={systemMetricsLoading}
        error={systemMetricsError}
      />

      {/* 通道告警统计和最近告警 */}
      <Row gutter={[14, 14]} className="dashboard-insights-row">
        <Col xs={24} lg={12}>
          <ChannelAlertChart />
        </Col>
        <Col xs={24} lg={12}>
          <RecentAlertCard
            title="最新告警"
            icon={<HistoryOutlined />}
            alerts={alerts}
            tasks={tasks}
            viewAllPath="/alerts"
            loading={loading}
          />
        </Col>
      </Row>
    </div>
  );
}
