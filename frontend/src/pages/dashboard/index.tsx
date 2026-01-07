import { useEffect, useState } from 'react';
import { Row, Col } from 'antd';
import {
  AppstoreOutlined,
  AlertOutlined,
  RocketOutlined,
  HistoryOutlined,
  ArrowUpOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  ExperimentOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { getVideoSources, getAlgorithms, getTodayAlertsCount, getAlerts } from '@/services/api';
import StatCard from './components/StatCard';
import QuickAccessCard from './components/QuickAccessCard';
import RecentAlertCard from './components/RecentAlertCard';
import WelcomeBanner from './components/WelcomeBanner';
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

  useEffect(() => {
    loadDashboardData();

    // 自动刷新（每30秒）
    const interval = setInterval(loadDashboardData, 30000);

    return () => clearInterval(interval);
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

  const quickAccessItems = [
    {
      title: '任务管理',
      description: '配置和管理分析任务',
      icon: <VideoCameraOutlined />,
      iconBgColor: '#000000',
      path: '/video-sources',
    },
    {
      title: '算法管理',
      description: '管理AI算法模型',
      icon: <ExperimentOutlined />,
      iconBgColor: '#000000',
      path: '/algorithms',
    },
    {
      title: '告警记录',
      description: '查看历史告警信息',
      icon: <AlertOutlined />,
      iconBgColor: '#ff4d4f',
      path: '/alerts',
    },
  ];

  return (
    <div className="dashboard-page">
      {/* 统计卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<AppstoreOutlined />}
            title="总任务数"
            value={stats.totalTasks}
            subtitle={`运行中: ${stats.runningTasks}`}
            iconBgColor="#000000"
            trendIcon={<ArrowUpOutlined />}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<ExperimentOutlined />}
            title="算法模型"
            value={stats.totalAlgorithms}
            subtitle="AI 分析引擎"
            iconBgColor="#000000"
            trendIcon={<CheckCircleOutlined />}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<AlertOutlined />}
            title="今日告警"
            value={stats.todayAlerts}
            subtitle="实时监控中"
            iconBgColor="#ff4d4f"
            trendIcon={<ExclamationCircleOutlined />}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<CheckCircleOutlined />}
            title="系统状态"
            value="运行中"
            subtitle="正常运行"
            iconBgColor="#52c41a"
            trendIcon={<CheckCircleOutlined />}
          />
        </Col>
      </Row>

      {/* 快捷操作和最近告警 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={12}>
          <QuickAccessCard
            title="快速访问"
            icon={<RocketOutlined />}
            items={quickAccessItems}
          />
        </Col>
        <Col xs={24} lg={12}>
          <RecentAlertCard
            title="最近告警"
            icon={<HistoryOutlined />}
            alerts={alerts}
            tasks={tasks}
            viewAllPath="/alerts"
            loading={loading}
          />
        </Col>
      </Row>

      {/* 欢迎横幅 */}
      <WelcomeBanner />
    </div>
  );
}
