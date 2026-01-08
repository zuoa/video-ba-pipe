import React, { useState, useCallback } from 'react';
import {
  Modal,
  Upload,
  Button,
  Alert,
  Space,
  Descriptions,
  Table,
  Image,
  message,
  Spin,
  Tabs,
  Tag,
  Statistic,
  Row,
  Col,
  Card,
  Progress,
  Divider,
  Tooltip,
  Empty,
} from 'antd';
import {
  PlayCircleOutlined,
  UploadOutlined,
  DeleteOutlined,
  InboxOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  InfoCircleOutlined,
  PictureOutlined,
  HistoryOutlined,
  DownloadOutlined,
  ReloadOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { UploadFile } from 'antd/es/upload/interface';
import type { Algorithm } from './AlgorithmTable';
import { testAlgorithm } from '@/services/api';
import './TestModal.css';

const { Dragger } = Upload;

export interface TestModalProps {
  visible: boolean;
  algorithm: Algorithm | null;
  onCancel: () => void;
}

interface DetectionResult {
  label: string;
  confidence: number;
  bbox: number[];
  label_name?: string;
  stages?: Array<{
    label_name: string;
    confidence: number;
  }>;
}

interface TestResponse {
  success: boolean;
  detection_count: number;
  result_image?: string;
  detections?: DetectionResult[];
  error?: string;
}

interface TestHistory {
  timestamp: number;
  result: TestResponse;
  image: string;
}

const TestModal: React.FC<TestModalProps> = ({ visible, algorithm, onCancel }) => {
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResponse | null>(null);
  const [previewImage, setPreviewImage] = useState<string>('');
  const [testHistory, setTestHistory] = useState<TestHistory[]>([]);
  const [activeTab, setActiveTab] = useState<'test' | 'history'>('test');

  const handleUploadChange = useCallback((info: any) => {
    const { fileList } = info;
    setFileList(fileList);

    if (fileList.length > 0 && fileList[0].originFileObj) {
      const file = fileList[0].originFileObj;
      const reader = new FileReader();
      reader.onload = (e) => {
        setPreviewImage(e.target?.result as string);
      };
      reader.readAsDataURL(file);
    } else {
      setPreviewImage('');
    }

    // Reset test result when file changes
    setTestResult(null);
  }, []);

  const handleRemoveImage = useCallback(() => {
    setFileList([]);
    setPreviewImage('');
    setTestResult(null);
  }, []);

  const handleTest = async () => {
    if (fileList.length === 0 || !algorithm) {
      message.warning('请先上传测试图片');
      return;
    }

    setTesting(true);
    setTestResult(null);

    try {
      const result = await testAlgorithm(algorithm.id, fileList[0].originFileObj as File);
      setTestResult(result);

      // Add to history
      if (previewImage) {
        setTestHistory(prev => [{
          timestamp: Date.now(),
          result,
          image: previewImage
        }, ...prev].slice(0, 10)); // Keep last 10 tests
      }

      if (result.success) {
        message.success({
          content: `检测完成，发现 ${result.detection_count} 个目标`,
          icon: <CheckCircleOutlined />
        });
      } else {
        message.error({
          content: result.error || '检测失败',
          icon: <CloseCircleOutlined />
        });
      }
    } catch (error: any) {
      const errorMsg = error?.response?.data?.error || '未知错误';
      message.error({
        content: errorMsg,
        icon: <CloseCircleOutlined />
      });
      setTestResult({
        success: false,
        detection_count: 0,
        error: errorMsg,
      });
    } finally {
      setTesting(false);
    }
  };

  const handleDownloadResult = useCallback(() => {
    if (!testResult?.result_image) return;

    const link = document.createElement('a');
    link.href = testResult.result_image;
    link.download = `test-result-${algorithm?.id}-${Date.now()}.jpg`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    message.success('图片已下载');
  }, [testResult, algorithm]);

  const handleRetest = useCallback(() => {
    setTestResult(null);
  }, []);

  const handleCancel = useCallback(() => {
    setFileList([]);
    setPreviewImage('');
    setTestResult(null);
    setTestHistory([]);
    setActiveTab('test');
    onCancel();
  }, [onCancel]);

  const detectionColumns = [
    {
      title: '序号',
      key: 'index',
      width: 60,
      align: 'center' as const,
      render: (_: any, __: any, index: number) => (
        <Tag color="blue">{index + 1}</Tag>
      ),
    },
    {
      title: '标签',
      dataIndex: 'label_name',
      key: 'label_name',
      width: 140,
      render: (label: string, record: DetectionResult) => (
        <Tag color="geekblue" className="detection-label-tag">
          {label || record.label || 'Object'}
        </Tag>
      ),
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 150,
      render: (confidence: number) => {
        const percent = confidence * 100;
        const color = percent >= 80 ? '#52c41a' : percent >= 60 ? '#faad14' : '#ff4d4f';
        return (
          <div className="detection-confidence-wrapper">
            <Progress
              percent={percent}
              size="small"
              strokeColor={color}
              format={(p) => `${p?.toFixed(1)}%`}
              showInfo={true}
            />
          </div>
        );
      },
    },
    {
      title: '位置',
      dataIndex: 'bbox',
      key: 'bbox',
      width: 180,
      render: (bbox: number[]) => {
        if (!bbox || !Array.isArray(bbox) || bbox.length < 4) {
          return <span style={{ color: '#999' }}>-</span>;
        }
        const [x1, y1, x2, y2] = bbox;
        const width = Math.round(x2 - x1);
        const height = Math.round(y2 - y1);
        return (
          <Tooltip title={`位置: (${Math.round(x1)}, ${Math.round(y1)})\n尺寸: ${width}x${height}`}>
            <code className="detection-bbox-code">
              [{Math.round(x1)}, {Math.round(y1)}, {Math.round(x2)}, {Math.round(y2)}]
            </code>
          </Tooltip>
        );
      },
    },
  ];

  // Calculate statistics from history
  const calculateStats = useCallback(() => {
    if (testHistory.length === 0) return null;

    const totalTests = testHistory.length;
    const successfulTests = testHistory.filter(h => h.result.success).length;
    const totalDetections = testHistory.reduce((sum, h) => sum + (h.result.detection_count || 0), 0);
    const avgDetections = totalDetections / totalTests;

    return { totalTests, successfulTests, totalDetections, avgDetections };
  }, [testHistory]);

  const stats = calculateStats();

  const renderTestTab = () => (
    <div className="test-modal-content">
      <Row gutter={24}>
        {/* Left Panel - Upload & Control */}
        <Col span={12}>
          <Card className="test-card upload-card" bordered={false}>
            <div className="card-header">
              <PictureOutlined />
              <h4>上传测试图片</h4>
            </div>

            {!previewImage ? (
              <Dragger
                fileList={fileList}
                onChange={handleUploadChange}
                beforeUpload={() => false}
                maxCount={1}
                accept="image/*"
                className="test-upload-dragger"
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">点击或拖拽上传图片</p>
                <p className="ant-upload-hint">支持 JPG、PNG、WEBP 格式，建议尺寸 ≥ 640x640</p>
              </Dragger>
            ) : (
              <div className="test-preview-container">
                <div className="test-preview-wrapper">
                  <Image
                    src={previewImage}
                    alt="预览"
                    className="test-preview-image"
                    preview={false}
                  />
                  <Button
                    danger
                    type="primary"
                    icon={<DeleteOutlined />}
                    onClick={handleRemoveImage}
                    className="test-remove-btn"
                  >
                    移除图片
                  </Button>
                </div>
              </div>
            )}

            <Space direction="vertical" style={{ width: '100%', marginTop: 16 }} size="middle">
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={handleTest}
                loading={testing}
                disabled={fileList.length === 0}
                block
                size="large"
                className="test-run-btn"
              >
                开始测试
              </Button>

              {testResult && (
                <Button
                  icon={<ReloadOutlined />}
                  onClick={handleRetest}
                  block
                >
                  重新测试
                </Button>
              )}
            </Space>
          </Card>

          {/* Algorithm Info */}
          {algorithm && (
            <Card className="test-card info-card" bordered={false} title={
              <Space>
                <InfoCircleOutlined />
                <span>算法信息</span>
              </Space>
            }>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="算法名称">{algorithm.name}</Descriptions.Item>
                <Descriptions.Item label="标签名称">{algorithm.label_name || '-'}</Descriptions.Item>
                <Descriptions.Item label="检测间隔">{algorithm.interval_seconds} 秒</Descriptions.Item>
                <Descriptions.Item label="运行超时">{algorithm.runtime_timeout || 30} 秒</Descriptions.Item>
                <Descriptions.Item label="内存限制">{algorithm.memory_limit_mb || 512} MB</Descriptions.Item>
              </Descriptions>
            </Card>
          )}
        </Col>

        {/* Right Panel - Results */}
        <Col span={12}>
          <Card
            className="test-card results-card"
            bordered={false}
            title={
              <Space>
                {testing ? (
                  <Spin size="small" />
                ) : testResult?.success ? (
                  <CheckCircleOutlined style={{ color: '#52c41a' }} />
                ) : testResult ? (
                  <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
                ) : (
                  <ThunderboltOutlined />
                )}
                <span>测试结果</span>
              </Space>
            }
          >
            {testing && (
              <div className="test-loading-state">
                <Spin size="large" />
                <p>正在运行检测算法...</p>
                <p className="loading-hint">这可能需要几秒钟</p>
              </div>
            )}

            {!testing && !testResult && (
              <div className="test-empty-state">
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    <span>
                      上传图片并点击"开始测试"<br />
                      查看检测结果
                    </span>
                  }
                />
              </div>
            )}

            {!testing && testResult && (
              <div className="test-results-content">
                {testResult.success ? (
                  <>
                    {/* Statistics */}
                    <Row gutter={16} className="test-stats-row">
                      <Col span={12}>
                        <Statistic
                          title="检测数量"
                          value={testResult.detection_count}
                          prefix={<CheckCircleOutlined />}
                          valueStyle={{ color: '#52c41a' }}
                        />
                      </Col>
                      <Col span={12}>
                        <Statistic
                          title="检测状态"
                          value="成功"
                          valueStyle={{ color: '#52c41a', fontSize: 16 }}
                        />
                      </Col>
                    </Row>

                    <Divider />

                    {/* Result Image */}
                    {testResult.result_image && (
                      <div className="test-result-image-container">
                        <div className="result-image-header">
                          <h5>检测结果可视化</h5>
                          <Button
                            type="primary"
                            size="small"
                            icon={<DownloadOutlined />}
                            onClick={handleDownloadResult}
                          >
                            下载结果
                          </Button>
                        </div>
                        <Image
                          src={testResult.result_image}
                          alt="检测结果"
                          className="test-result-image"
                        />
                      </div>
                    )}

                    {/* Detection Details */}
                    {testResult.detections && testResult.detections.length > 0 && (
                      <div className="test-detection-details">
                        <Divider>检测详情</Divider>
                        <Table
                          dataSource={testResult.detections}
                          columns={detectionColumns}
                          pagination={false}
                          size="small"
                          className="test-detection-table"
                          scroll={{ y: 240 }}
                        />
                      </div>
                    )}

                    {testResult.detections && testResult.detections.length === 0 && (
                      <Alert
                        message="未检测到任何目标"
                        description="算法已成功运行，但在图片中未发现任何目标"
                        type="info"
                        showIcon
                        icon={<InfoCircleOutlined />}
                      />
                    )}
                  </>
                ) : (
                  <Alert
                    message="测试失败"
                    description={testResult.error || '未知错误'}
                    type="error"
                    showIcon
                    icon={<CloseCircleOutlined />}
                  />
                )}
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );

  const renderHistoryTab = () => (
    <div className="test-history-content">
      {testHistory.length === 0 ? (
        <Empty
          description={
            <span>
              暂无测试历史<br />
              完成测试后将自动记录
            </span>
          }
        />
      ) : (
        <>
          {stats && (
            <Card className="history-stats-card" bordered={false}>
              <Row gutter={16}>
                <Col span={6}>
                  <Statistic title="总测试次数" value={stats.totalTests} />
                </Col>
                <Col span={6}>
                  <Statistic
                    title="成功次数"
                    value={stats.successfulTests}
                    suffix={`/ ${stats.totalTests}`}
                    valueStyle={{ color: '#52c41a' }}
                  />
                </Col>
                <Col span={6}>
                  <Statistic
                    title="总检测数"
                    value={stats.totalDetections}
                    valueStyle={{ color: '#1890ff' }}
                  />
                </Col>
                <Col span={6}>
                  <Statistic
                    title="平均检测数"
                    value={stats.avgDetections.toFixed(1)}
                    precision={1}
                    valueStyle={{ color: '#722ed1' }}
                  />
                </Col>
              </Row>
            </Card>
          )}

          <div className="history-list">
            {testHistory.map((item, index) => (
              <Card key={item.timestamp} className="history-item-card" bordered={false}>
                <Space direction="vertical" style={{ width: '100%' }} size="small">
                  <div className="history-item-header">
                    <Space>
                      <Tag color={item.result.success ? 'success' : 'error'}>
                        测试 #{testHistory.length - index}
                      </Tag>
                      <span className="history-timestamp">
                        {new Date(item.timestamp).toLocaleString('zh-CN')}
                      </span>
                    </Space>
                    <Space>
                      <Statistic
                        title="检测数"
                        value={item.result.detection_count}
                        valueStyle={{ fontSize: 16 }}
                      />
                    </Space>
                  </div>
                  {item.result.result_image && (
                    <Image
                      src={item.result.result_image}
                      alt={`测试结果 #${testHistory.length - index}`}
                      className="history-result-image"
                    />
                  )}
                  {item.result.detections && item.result.detections.length > 0 && (
                    <div className="history-detections-summary">
                      <Space wrap>
                        {item.result.detections.map((det, i) => (
                          <Tag key={i} color="blue">
                            {det.label_name || det.label || 'Object'}: {(det.confidence * 100).toFixed(0)}%
                          </Tag>
                        ))}
                      </Space>
                    </div>
                  )}
                </Space>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );

  const tabItems = [
    {
      key: 'test',
      label: (
        <span>
          <PlayCircleOutlined />
          测试
        </span>
      ),
      children: renderTestTab(),
    },
    {
      key: 'history',
      label: (
        <span>
          <HistoryOutlined />
          历史 {testHistory.length > 0 && <Tag>{testHistory.length}</Tag>}
        </span>
      ),
      children: renderHistoryTab(),
    },
  ];

  return (
    <Modal
      title={
        <div className="test-modal-title">
          <div className="title-icon">
            <PlayCircleOutlined />
          </div>
          <div className="title-content">
            <span className="title-text">算法测试</span>
            {algorithm && (
              <span className="title-subtitle">
                {algorithm.name} (ID: {algorithm.id})
              </span>
            )}
          </div>
        </div>
      }
      open={visible}
      onCancel={handleCancel}
      footer={null}
      width={1200}
      className="test-modal"
    >
      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as 'test' | 'history')}
        items={tabItems}
        className="test-tabs"
      />
    </Modal>
  );
};

export default TestModal;
