import React, { useState } from 'react';
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
} from 'antd';
import {
  PlayCircleOutlined,
  UploadOutlined,
  DeleteOutlined,
  InboxOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  InfoCircleOutlined,
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

const TestModal: React.FC<TestModalProps> = ({ visible, algorithm, onCancel }) => {
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResponse | null>(null);
  const [previewImage, setPreviewImage] = useState<string>('');

  const handleUploadChange = (info: any) => {
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
  };

  const handleRemoveImage = () => {
    setFileList([]);
    setPreviewImage('');
    setTestResult(null);
  };

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

      if (result.success) {
        message.success(`检测完成，发现 ${result.detection_count} 个目标`);
      } else {
        message.error(result.error || '检测失败');
      }
    } catch (error: any) {
      message.error(error?.response?.data?.error || '测试失败');
      setTestResult({
        success: false,
        detection_count: 0,
        error: error?.response?.data?.error || '未知错误',
      });
    } finally {
      setTesting(false);
    }
  };

  const handleCancel = () => {
    setFileList([]);
    setPreviewImage('');
    setTestResult(null);
    onCancel();
  };

  const detectionColumns = [
    {
      title: '序号',
      key: 'index',
      width: 60,
      render: (_: any, __: any, index: number) => index + 1,
    },
    {
      title: '标签',
      dataIndex: 'label_name',
      key: 'label_name',
      width: 120,
      render: (label: string, record: DetectionResult) => (
        <span className="detection-label">
          {label || record.label || 'Object'}
        </span>
      ),
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 100,
      render: (confidence: number) => (
        <span className="detection-confidence">
          {(confidence * 100).toFixed(1)}%
        </span>
      ),
    },
    {
      title: '位置',
      dataIndex: 'bbox',
      key: 'bbox',
      render: (bbox: number[]) => (
        <code className="detection-bbox">
          [{bbox.map((v) => Math.round(v)).join(', ')}]
        </code>
      ),
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
      width={1000}
      className="test-modal"
    >
      <div className="test-modal-content">
        <div className="test-grid">
          {/* Left Panel - Upload */}
          <div className="test-left-panel">
            <div className="test-section">
              <h4 className="test-section-title">
                <UploadOutlined />
                上传测试图片
              </h4>

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
                  <p className="ant-upload-hint">支持 JPG、PNG、GIF 格式，最大 10MB</p>
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
                      icon={<DeleteOutlined />}
                      onClick={handleRemoveImage}
                      className="test-remove-btn"
                    >
                      移除图片
                    </Button>
                  </div>
                </div>
              )}

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
            </div>

            {/* Algorithm Info */}
            {algorithm && (
              <div className="test-section">
                <h4 className="test-section-title">
                  <InfoCircleOutlined />
                  算法信息
                </h4>
                <Descriptions
                  column={1}
                  size="small"
                  bordered
                  className="test-algorithm-info"
                >
                  <Descriptions.Item label="名称">
                    {algorithm.name}
                  </Descriptions.Item>
                  <Descriptions.Item label="标签">
                    {algorithm.label_name}
                  </Descriptions.Item>
                  <Descriptions.Item label="间隔">
                    {algorithm.interval_seconds}秒
                  </Descriptions.Item>
                  <Descriptions.Item label="插件">
                    {algorithm.plugin_module || algorithm.script_path || '-'}
                  </Descriptions.Item>
                </Descriptions>
              </div>
            )}
          </div>

          {/* Right Panel - Results */}
          <div className="test-right-panel">
            <h4 className="test-section-title">
              {testing ? (
                <Spin size="small" />
              ) : testResult?.success ? (
                <CheckCircleOutlined style={{ color: '#52c41a' }} />
              ) : testResult ? (
                <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
              ) : (
                <InfoCircleOutlined />
              )}
              测试结果
            </h4>

            <div className="test-results-container">
              {testing && (
                <div className="test-loading-state">
                  <Spin size="large" />
                  <p>正在处理...</p>
                </div>
              )}

              {!testing && !testResult && (
                <div className="test-empty-state">
                  <InboxOutlined />
                  <p>上传图片后将显示测试结果</p>
                </div>
              )}

              {!testing && testResult && (
                <div className="test-results-content">
                  {testResult.success ? (
                    <>
                      {/* Alert Summary */}
                      <Alert
                        message={
                          <Space>
                            <span>检测完成</span>
                            <strong>发现 {testResult.detection_count} 个目标</strong>
                          </Space>
                        }
                        type="success"
                        showIcon
                        className="test-result-alert"
                      />

                      {/* Result Image */}
                      {testResult.result_image && (
                        <div className="test-result-image-container">
                          <h5 className="test-result-subtitle">检测可视化</h5>
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
                          <h5 className="test-result-subtitle">
                            检测详情 ({testResult.detections.length}个目标)
                          </h5>
                          <Table
                            dataSource={testResult.detections}
                            columns={detectionColumns}
                            pagination={false}
                            size="small"
                            className="test-detection-table"
                            scroll={{ y: 300 }}
                          />
                        </div>
                      )}

                      {testResult.detections && testResult.detections.length === 0 && (
                        <Alert
                          message="未检测到任何目标"
                          type="info"
                          showIcon
                          className="test-no-detection-alert"
                        />
                      )}
                    </>
                  ) : (
                    <Alert
                      message="测试失败"
                      description={testResult.error || '未知错误'}
                      type="error"
                      showIcon
                      className="test-error-alert"
                    />
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
};

export default TestModal;
