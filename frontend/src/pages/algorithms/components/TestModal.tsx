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
  Collapse,
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
  BugOutlined,
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
  metadata?: {
    model_count?: number;
    inference_time_ms?: number;
    total_detections?: number;
    roi_mode?: string;
    model_debug_info?: Array<{
      model_name: string;
      model_path: string;
      success: boolean;
      detections_count?: number;
      detections?: Array<{
        box: number[];
        confidence: number;
        class: number;
        class_name: string;
      }>;
      confidence_threshold?: number;
      class_filter?: number[];
      error?: string;
    }>;
    merge_debug_info?: {
      total_models: number;
      iou_threshold: number;
      detection_groups: number;
      model_names: string[];
    };
  };
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

                    {/* Multi-Model Debug Info */}
                    {testResult.metadata?.model_debug_info && testResult.metadata.model_debug_info.length > 0 && (
                      <div className="test-debug-details">
                        <Divider>
                          <Space>
                            <BugOutlined />
                            多模型调试信息
                          </Space>
                        </Divider>
                        <Collapse
                          defaultActiveKey={testResult.detection_count === 0 ? ['1'] : []}
                          size="small"
                          items={[
                            {
                              key: '1',
                              label: <span>查看每个模型的详细检测情况</span>,
                              children: (
                                <div className="debug-info-content">
                                  {testResult.metadata.model_debug_info.map((modelInfo, idx) => (
                                    <Card
                                      key={idx}
                                      size="small"
                                      title={
                                        <Space>
                                          <Tag color={modelInfo.success ? 'success' : 'error'}>
                                            {modelInfo.model_name}
                                          </Tag>
                                          <span style={{ fontSize: 12 }}>
                                            {modelInfo.model_path.split('/').pop()}
                                          </span>
                                        </Space>
                                      }
                                      style={{ marginBottom: 8 }}
                                    >
                                      {!modelInfo.success ? (
                                        <Alert
                                          type="error"
                                          message="模型推理失败"
                                          description={modelInfo.error}
                                          showIcon
                                          size="small"
                                        />
                                      ) : (
                                        <>
                                          <Descriptions size="small" column={2}>
                                            <Descriptions.Item label="检测数量">
                                              <Tag color="blue">{modelInfo.detections_count}</Tag>
                                            </Descriptions.Item>
                                            <Descriptions.Item label="置信度阈值">
                                              {modelInfo.confidence_threshold}
                                            </Descriptions.Item>
                                            <Descriptions.Item label="类别过滤" span={2}>
                                              {modelInfo.class_filter?.join(', ') || '全部'}
                                            </Descriptions.Item>
                                          </Descriptions>

                                          {modelInfo.detections && modelInfo.detections.length > 0 && (
                                            <Table
                                              size="small"
                                              dataSource={modelInfo.detections.map((det, i) => ({
                                                key: i,
                                                ...det
                                              }))}
                                              pagination={false}
                                              columns={[
                                                {
                                                  title: '序号',
                                                  width: 50,
                                                  render: (_: any, __: any, i: number) => i + 1
                                                },
                                                {
                                                  title: '类别',
                                                  dataIndex: 'class_name',
                                                  width: 80,
                                                  render: (name: string) => <Tag>{name}</Tag>
                                                },
                                                {
                                                  title: '置信度',
                                                  dataIndex: 'confidence',
                                                  width: 100,
                                                  render: (conf: number) => `${(conf * 100).toFixed(1)}%`
                                                },
                                                {
                                                  title: '位置',
                                                  dataIndex: 'box',
                                                  render: (box: number[]) => {
                                                    if (!box || box.length < 4) return '-';
                                                    return `[${box[0].toFixed(0)}, ${box[1].toFixed(0)}, ${box[2].toFixed(0)}, ${box[3].toFixed(0)}]`;
                                                  }
                                                }
                                              ]}
                                              scroll={{ y: 150 }}
                                            />
                                          )}

                                          {modelInfo.detections_count === 0 && (
                                            <Alert
                                              type="info"
                                              message="该模型未检测到任何目标"
                                              description="可能原因：置信度阈值过高、类别过滤不匹配、目标不在此模型检测范围内"
                                              showIcon
                                              size="small"
                                            />
                                          )}
                                        </>
                                      )}
                                    </Card>
                                  ))}

                                  {testResult.metadata.merge_debug_info && (
                                    <>
                                      <Divider style={{ margin: '12px 0' }}>IOU合并信息</Divider>
                                      <Descriptions size="small" column={2}>
                                        <Descriptions.Item label="参与合并模型数">
                                          {testResult.metadata.merge_debug_info.total_models}
                                        </Descriptions.Item>
                                        <Descriptions.Item label="IOU阈值">
                                          {testResult.metadata.merge_debug_info.iou_threshold}
                                        </Descriptions.Item>
                                        <Descriptions.Item label="合并后组数">
                                          <Tag color={testResult.metadata.merge_debug_info.detection_groups > 0 ? 'success' : 'warning'}>
                                            {testResult.metadata.merge_debug_info.detection_groups}
                                          </Tag>
                                        </Descriptions.Item>
                                        <Descriptions.Item label="模型名称">
                                          {testResult.metadata.merge_debug_info.model_names.join(', ')}
                                        </Descriptions.Item>
                                      </Descriptions>

                                      {testResult.metadata.merge_debug_info.detection_groups === 0 && (
                                        <Alert
                                          type="warning"
                                          message="多模型IOU合并失败"
                                          description={
                                            <div>
                                              <p>虽然各模型分别检测到了目标，但无法通过IOU匹配合并为确认目标。</p>
                                              <p>可能原因：</p>
                                              <ul style={{ marginLeft: 20, marginTop: 8 }}>
                                                <li>各模型检测到的目标位置差异较大</li>
                                                <li>IOU阈值设置过高（当前为 {testResult.metadata.merge_debug_info.iou_threshold}）</li>
                                                <li>尝试降低 IOU 阈值或调整模型的扩展比例（expand_width/expand_height）</li>
                                              </ul>
                                            </div>
                                          }
                                          showIcon
                                          style={{ marginTop: 8 }}
                                        />
                                      )}
                                    </>
                                  )}
                                </div>
                              )
                            }
                          ]}
                        />
                      </div>
                    )}

                    {/* Single-Model Debug Info (for simple_yolo_detector) */}
                    {testResult.metadata?.detections_detail && !testResult.metadata?.model_debug_info && (
                      <div className="test-debug-details">
                        <Divider>
                          <Space>
                            <BugOutlined />
                            检测调试信息
                          </Space>
                        </Divider>
                        <Collapse
                          defaultActiveKey={testResult.detection_count === 0 ? ['1'] : []}
                          size="small"
                          items={[
                            {
                              key: '1',
                              label: <span>查看检测详情</span>,
                              children: (
                                <div className="debug-info-content">
                                  <Card size="small" title="检测配置" style={{ marginBottom: 8 }}>
                                    <Descriptions size="small" column={2}>
                                      <Descriptions.Item label="模型路径">
                                        <span style={{ fontSize: 12 }}>
                                          {testResult.metadata.model_path?.split('/').pop() || 'unknown'}
                                        </span>
                                      </Descriptions.Item>
                                      <Descriptions.Item label="置信度阈值">
                                        {testResult.metadata.confidence_threshold}
                                      </Descriptions.Item>
                                      <Descriptions.Item label="类别过滤">
                                        {Array.isArray(testResult.metadata.class_filter)
                                          ? testResult.metadata.class_filter.join(', ')
                                          : testResult.metadata.class_filter || '全部'}
                                      </Descriptions.Item>
                                      <Descriptions.Item label="推理时间">
                                        {testResult.metadata.inference_time_ms?.toFixed(1)} ms
                                      </Descriptions.Item>
                                      <Descriptions.Item label="图像尺寸">
                                        {testResult.metadata.image_size?.width} x {testResult.metadata.image_size?.height}
                                      </Descriptions.Item>
                                    </Descriptions>
                                  </Card>

                                  {testResult.metadata.detections_detail.length > 0 ? (
                                    <Card size="small" title="所有检测结果" style={{ marginBottom: 8 }}>
                                      <Table
                                        size="small"
                                        dataSource={testResult.metadata.detections_detail.map((det: any, i: number) => ({
                                          key: i,
                                          ...det
                                        }))}
                                        pagination={false}
                                        columns={[
                                          {
                                            title: '序号',
                                            width: 50,
                                            render: (_: any, __: any, i: number) => i + 1
                                          },
                                          {
                                            title: '类别',
                                            dataIndex: 'class_name',
                                            width: 80,
                                            render: (name: string) => <Tag>{name}</Tag>
                                          },
                                          {
                                            title: '置信度',
                                            dataIndex: 'confidence',
                                            width: 100,
                                            render: (conf: number) => `${(conf * 100).toFixed(1)}%`
                                          },
                                          {
                                            title: '位置',
                                            dataIndex: 'box',
                                            render: (box: number[]) => {
                                              if (!box || box.length < 4) return '-';
                                              return `[${box[0].toFixed(0)}, ${box[1].toFixed(0)}, ${box[2].toFixed(0)}, ${box[3].toFixed(0)}]`;
                                            }
                                          }
                                        ]}
                                        scroll={{ y: 200 }}
                                      />
                                    </Card>
                                  ) : (
                                    <Alert
                                      type="info"
                                      message="未检测到任何目标"
                                      description={
                                        <div>
                                          <p>模型已成功运行，但在图片中未发现任何目标。</p>
                                          <p>可能原因：</p>
                                          <ul style={{ marginLeft: 20, marginTop: 8 }}>
                                            <li>置信度阈值过高（当前为 {testResult.metadata.confidence_threshold}），建议降低到 0.3-0.5</li>
                                            <li>类别过滤配置不匹配，当前过滤：{Array.isArray(testResult.metadata.class_filter) ? testResult.metadata.class_filter.join(', ') : '全部'}</li>
                                            <li>图片中确实没有目标物体</li>
                                            <li>模型训练数据不包含此类目标</li>
                                          </ul>
                                        </div>
                                      }
                                      showIcon
                                    />
                                  )}
                                </div>
                              )
                            }
                          ]}
                        />
                      </div>
                    )}


                    {testResult.detections && testResult.detections.length === 0 && (
                      <>
                        {testResult.metadata?.model_debug_info && testResult.metadata.model_debug_info.length > 1 ? (
                          <Alert
                            message="多模型检测未确认任何目标"
                            description={
                              <div>
                                <p>算法已成功运行，但多模型未共同确认任何目标。</p>
                                <p>请查看下方的"多模型调试信息"了解每个模型的检测情况，可能的原因：</p>
                                <ul style={{ marginLeft: 20, marginTop: 8 }}>
                                  <li>各模型检测到的目标位置差异较大，无法通过IOU匹配合并</li>
                                  <li>部分模型未检测到目标（置信度阈值过高或类别不匹配）</li>
                                  <li>IOU阈值设置过高，建议降低阈值或调整扩展比例</li>
                                </ul>
                              </div>
                            }
                            type="warning"
                            showIcon
                            icon={<BugOutlined />}
                          />
                        ) : testResult.metadata?.detections_detail ? (
                          <Alert
                            message="未检测到任何目标"
                            description={
                              <div>
                                <p>算法已成功运行，但在图片中未发现任何目标。</p>
                                <p>请查看下方的"检测调试信息"了解详细情况。</p>
                              </div>
                            }
                            type="info"
                            showIcon
                            icon={<BugOutlined />}
                          />
                        ) : (
                          <Alert
                            message="未检测到任何目标"
                            description="算法已成功运行，但在图片中未发现任何目标"
                            type="info"
                            showIcon
                            icon={<InfoCircleOutlined />}
                          />
                        )}
                      </>
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
