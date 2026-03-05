import React, { useCallback, useEffect, useState } from 'react';
import { Row, Col, message, Card, Space, Select, Button } from 'antd';
import { ExperimentOutlined, ReloadOutlined } from '@ant-design/icons';
import { getVideoSources, getWorkflows, getWorkflowTestResults } from '@/services/api';
import { PageHeader } from '@/components/common';
import { Alert, Task, Workflow } from '../alerts/types';
import AlertCard from '../alerts/components/AlertCard';
import AlertDetailModal from '../alerts/components/AlertDetailModal';
import PaginationBar from '../alerts/components/PaginationBar';
import EmptyState from '../alerts/components/EmptyState';
import './index.css';

const WorkflowTestResultsPage: React.FC = () => {
  const [records, setRecords] = useState<Alert[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(false);
  const [workflowId, setWorkflowId] = useState<string | undefined>();
  const [mediaType, setMediaType] = useState<string | undefined>();
  const [timeRange, setTimeRange] = useState<string>('7d');
  const [detailVisible, setDetailVisible] = useState(false);
  const [selectedRecordIndex, setSelectedRecordIndex] = useState(0);

  const [pagination, setPagination] = useState({
    page: 1,
    per_page: 20,
    total: 0,
  });

  const formatLocalDateTime = (date: Date) => {
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  };

  const buildTimeRangeParams = () => {
    if (!timeRange || timeRange === 'all') {
      return {};
    }

    const now = new Date();
    let startTime = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);

    if (timeRange === '24h') {
      startTime = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    } else if (timeRange === '30d') {
      startTime = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    }

    return {
      start_time: formatLocalDateTime(startTime),
      end_time: formatLocalDateTime(now),
    };
  };

  const loadBaseData = useCallback(async () => {
    try {
      const [taskData, workflowData] = await Promise.all([
        getVideoSources(),
        getWorkflows(),
      ]);
      setTasks(taskData || []);
      setWorkflows(workflowData || []);
    } catch (error: any) {
      message.error('加载测试结果基础数据失败: ' + error.message);
    }
  }, []);

  const loadRecords = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = {
        page: pagination.page,
        per_page: pagination.per_page,
        ...buildTimeRangeParams(),
      };

      if (workflowId) {
        params.workflow_id = workflowId;
      }

      if (mediaType) {
        params.media_type = mediaType;
      }

      const response = await getWorkflowTestResults(params);

      const normalized: Alert[] = (response.data || []).map((item: any) => ({
        ...item,
        alert_type: item.success ? item.alert_type : 'error',
      }));

      setRecords(normalized);
      setPagination(prev => ({
        ...prev,
        total: response.pagination?.total || 0,
        page: response.pagination?.page || 1,
        per_page: response.pagination?.per_page || 20,
      }));
    } catch (error: any) {
      message.error('加载测试结果失败: ' + error.message);
      setRecords([]);
    } finally {
      setLoading(false);
    }
  }, [pagination.page, pagination.per_page, workflowId, mediaType, timeRange]);

  useEffect(() => {
    loadBaseData();
  }, [loadBaseData]);

  useEffect(() => {
    loadRecords();
  }, [loadRecords]);

  useEffect(() => {
    const timer = setInterval(() => {
      loadRecords();
    }, 30000);

    return () => clearInterval(timer);
  }, [loadRecords]);

  const showDetail = (recordId: number) => {
    const index = records.findIndex(a => a.id === recordId);
    if (index !== -1) {
      setSelectedRecordIndex(index);
      setDetailVisible(true);
    }
  };

  const handleNavigate = (direction: 'prev' | 'next') => {
    if (direction === 'prev' && selectedRecordIndex > 0) {
      setSelectedRecordIndex(selectedRecordIndex - 1);
    } else if (direction === 'next' && selectedRecordIndex < records.length - 1) {
      setSelectedRecordIndex(selectedRecordIndex + 1);
    }
  };

  const selectedRecord = records[selectedRecordIndex];

  return (
    <div className="workflow-test-results-page">
      <PageHeader
        icon={<ExperimentOutlined />}
        title="编排测试结果"
        subtitle="测试数据独立存储，不计入告警中心统计"
        count={pagination.total}
        countLabel="条测试记录"
      />

      <Card className="workflow-test-filter-card">
        <Space size="middle" wrap>
          <Select
            allowClear
            placeholder="筛选工作流"
            style={{ width: 220 }}
            value={workflowId}
            onChange={(value) => {
              setWorkflowId(value);
              setPagination(prev => ({ ...prev, page: 1 }));
            }}
            options={workflows.map(w => ({ label: w.name, value: String(w.id) }))}
          />

          <Select
            allowClear
            placeholder="输入类型"
            style={{ width: 160 }}
            value={mediaType}
            onChange={(value) => {
              setMediaType(value);
              setPagination(prev => ({ ...prev, page: 1 }));
            }}
            options={[
              { label: '图片测试', value: 'image' },
              { label: '视频测试', value: 'video' },
            ]}
          />

          <Select
            style={{ width: 160 }}
            value={timeRange}
            onChange={(value) => {
              setTimeRange(value);
              setPagination(prev => ({ ...prev, page: 1 }));
            }}
            options={[
              { label: '近24小时', value: '24h' },
              { label: '近7天', value: '7d' },
              { label: '近30天', value: '30d' },
              { label: '全部时间', value: 'all' },
            ]}
          />

          <Button icon={<ReloadOutlined />} loading={loading} onClick={loadRecords}>
            刷新
          </Button>
        </Space>
      </Card>

      {records.length > 0 && (
        <PaginationBar
          current={pagination.page}
          pageSize={pagination.per_page}
          total={pagination.total}
          onChange={(page, pageSize) => {
            setPagination(prev => ({ ...prev, page, per_page: pageSize || prev.per_page }));
          }}
          position="top"
        />
      )}

      {records.length === 0 ? (
        <EmptyState type="alerts" onRefresh={loadRecords} />
      ) : (
        <Row gutter={[16, 16]}>
          {records.map(record => (
            <Col key={record.id} xs={24} sm={12} lg={8} xl={6}>
              <AlertCard
                alert={record}
                task={tasks.find(t => t.id === record.task_id)}
                onClick={() => showDetail(record.id)}
              />
            </Col>
          ))}
        </Row>
      )}

      {records.length > 0 && (
        <PaginationBar
          current={pagination.page}
          pageSize={pagination.per_page}
          total={pagination.total}
          onChange={(page, pageSize) => {
            setPagination(prev => ({ ...prev, page, per_page: pageSize || prev.per_page }));
          }}
          position="bottom"
        />
      )}

      <AlertDetailModal
        visible={detailVisible}
        alert={selectedRecord || null}
        tasks={tasks}
        currentIndex={selectedRecordIndex}
        total={records.length}
        onClose={() => setDetailVisible(false)}
        onNavigate={handleNavigate}
      />
    </div>
  );
};

export default WorkflowTestResultsPage;
