import React, { useEffect, useMemo, useState } from 'react';
import { Upload, Button, Empty, Alert, Space, Image, message, Tag } from 'antd';
import {
  UploadOutlined,
  PlayCircleOutlined,
  VideoCameraOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  InfoCircleOutlined,
  LoadingOutlined,
  ClockCircleOutlined,
  FileImageOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'umi';
import { captureFrame, testWorkflow, testWorkflowWithFile } from '@/services/api';
import TestResultModal from './TestResultModal';
import './TestPanel.css';

const { Dragger } = Upload;

export interface TestPanelProps {
  workflow: any;
  nodes?: any[];
  edges?: any[];
  videoSources?: any[];
  onBeforeTest?: () => Promise<boolean | void>;
}

type InputMode = 'upload-image' | 'upload-video' | 'video-source';

const TestPanel: React.FC<TestPanelProps> = ({ workflow, nodes = [], edges = [], videoSources = [], onBeforeTest }) => {
  const navigate = useNavigate();
  const [inputMode, setInputMode] = useState<InputMode>('video-source');
  const [testImage, setTestImage] = useState<string | null>(null);
  const [testVideoFile, setTestVideoFile] = useState<File | null>(null);
  const [testVideoUrl, setTestVideoUrl] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [showResultModal, setShowResultModal] = useState(false);

  const workflowVideoSourceNode = useMemo(() => {
    return nodes.find(node =>
      node.type === 'videoSource' || node.data?.type === 'videoSource' || node.data?.type === 'source'
    );
  }, [nodes]);

  const workflowVideoSourceId = useMemo(() => {
    return workflowVideoSourceNode?.data?.videoSourceId;
  }, [workflowVideoSourceNode]);

  const videoSourceInfo = useMemo(() => {
    if (!workflowVideoSourceId) return null;
    return videoSources.find(s => s.id == workflowVideoSourceId);
  }, [workflowVideoSourceId, videoSources]);

  const canUseVideoSource = Boolean(workflowVideoSourceId && videoSourceInfo);

  useEffect(() => {
    return () => {
      if (testVideoUrl) {
        URL.revokeObjectURL(testVideoUrl);
      }
    };
  }, [testVideoUrl]);

  const handleImageUpload = (file: File) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      setTestImage(e.target?.result as string);
      setTestVideoFile(null);
      if (testVideoUrl) {
        URL.revokeObjectURL(testVideoUrl);
      }
      setTestVideoUrl(null);
      setTestResult(null);
    };
    reader.readAsDataURL(file);
    return false;
  };

  const handleVideoUpload = (file: File) => {
    setTestVideoFile(file);
    setTestImage(null);
    if (testVideoUrl) {
      URL.revokeObjectURL(testVideoUrl);
    }
    setTestVideoUrl(URL.createObjectURL(file));
    setTestResult(null);
    return false;
  };

  const handleCaptureFrame = async () => {
    if (!workflowVideoSourceId) {
      message.error('工作流中没有配置视频源');
      return;
    }

    setCapturing(true);
    try {
      const response = await captureFrame(workflowVideoSourceId);

      if (response.error) {
        throw new Error(response.error);
      }

      if (response.success && response.image) {
        setTestImage(response.image);
        setTestVideoFile(null);
        if (testVideoUrl) {
          URL.revokeObjectURL(testVideoUrl);
        }
        setTestVideoUrl(null);
        setTestResult(null);
        message.success(`抓帧成功 (${response.resolution || ''})`);
      } else {
        throw new Error('无效的响应数据');
      }
    } catch (error: any) {
      message.error(error.message || '抓帧失败，请检查视频源连接');
    } finally {
      setCapturing(false);
    }
  };

  const handleRunTest = async () => {
    if (!workflow?.id) {
      message.error('工作流ID不存在');
      return;
    }

    if (!testImage && !testVideoFile) {
      message.warning('请先上传测试图片/视频，或从视频源抓帧');
      return;
    }

    try {
      if (onBeforeTest) {
        const saveOk = await onBeforeTest();
        if (saveOk === false) {
          message.error('保存失败，无法开始测试');
          return;
        }
      }

      setTesting(true);
      setTestResult(null);

      let response: any;

      if (testVideoFile) {
        response = await testWorkflowWithFile(workflow.id, testVideoFile);
      } else {
        response = await testWorkflow(workflow.id, testImage as string);
      }

      const isSuccess = response.success !== false && (response.success || response.nodes);
      if (isSuccess) {
        setTestResult(response);
        if (response.nodes?.length) {
          setShowResultModal(true);
        }

        const recordId = response.test_record_id;
        const suffix = recordId ? `（记录 #${recordId}）` : '';
        message.success(`测试完成${suffix}`);
      } else {
        setTestResult({
          success: false,
          error: response.error || '测试失败',
          details: response.traceback,
        });
        message.error('测试失败: ' + (response.error || '未知错误'));
      }
    } catch (error: any) {
      setTestResult({
        success: false,
        error: error.message || '测试过程中出现错误',
        details: error.stack,
      });
      message.error('测试失败: ' + error.message);
    } finally {
      setTesting(false);
    }
  };

  const handleClearMedia = () => {
    setTestImage(null);
    setTestVideoFile(null);
    if (testVideoUrl) {
      URL.revokeObjectURL(testVideoUrl);
    }
    setTestVideoUrl(null);
    setTestResult(null);
  };

  const canRunTest = Boolean(testImage || testVideoFile);

  return (
    <div className="test-panel">
      <div className="panel-header">
        <PlayCircleOutlined className="panel-icon" />
        <span className="panel-title">算法编排测试</span>
      </div>

      <div className="test-content">
        {workflowVideoSourceNode && videoSourceInfo && (
          <div className="test-section workflow-info-section">
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <div className="info-title">
                <InfoCircleOutlined style={{ marginRight: 6, color: '#1890ff' }} />
                <span>工作流视频源</span>
              </div>
              <div className="video-source-card">
                <div className="video-source-name">{videoSourceInfo.name}</div>
                <div className="video-source-meta">
                  <Tag color="blue" style={{ margin: 0 }}>
                    {videoSourceInfo.source_code || videoSourceInfo.type}
                  </Tag>
                  {videoSourceInfo.url && (
                    <span className="video-source-url">{videoSourceInfo.url}</span>
                  )}
                </div>
              </div>
            </Space>
          </div>
        )}

        <div className="test-section">
          <div className="section-label">测试输入</div>
          <div className="source-tabs">
            <Button
              size="small"
              type={inputMode === 'upload-image' ? 'primary' : 'default'}
              icon={<FileImageOutlined />}
              onClick={() => {
                setInputMode('upload-image');
                setTestResult(null);
              }}
            >
              上传图片
            </Button>
            <Button
              size="small"
              type={inputMode === 'upload-video' ? 'primary' : 'default'}
              icon={<UploadOutlined />}
              onClick={() => {
                setInputMode('upload-video');
                setTestResult(null);
              }}
            >
              上传视频
            </Button>
            <Button
              size="small"
              type={inputMode === 'video-source' ? 'primary' : 'default'}
              icon={<VideoCameraOutlined />}
              onClick={() => {
                setInputMode('video-source');
                setTestResult(null);
              }}
            >
              视频源抓帧
            </Button>
          </div>
        </div>

        {inputMode === 'upload-image' && !testImage && !testVideoFile && (
          <div className="test-section">
            <Dragger
              accept="image/*"
              showUploadList={false}
              beforeUpload={handleImageUpload}
              className="upload-dragger"
            >
              <p className="ant-upload-drag-icon">
                <UploadOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽上传测试图片</p>
              <p className="ant-upload-hint">支持 JPG、PNG 等格式</p>
            </Dragger>
          </div>
        )}

        {inputMode === 'upload-video' && !testImage && !testVideoFile && (
          <div className="test-section">
            <Dragger
              accept="video/*"
              showUploadList={false}
              beforeUpload={handleVideoUpload}
              className="upload-dragger"
            >
              <p className="ant-upload-drag-icon">
                <VideoCameraOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽上传测试视频</p>
              <p className="ant-upload-hint">系统将抽样多帧执行编排测试</p>
            </Dragger>
          </div>
        )}

        {inputMode === 'video-source' && !testImage && !testVideoFile && (
          <div className="test-section">
            {!canUseVideoSource ? (
              <Alert
                type="warning"
                message="工作流未配置视频源"
                description="请在工作流中添加视频源节点后再使用此功能"
                showIcon
              />
            ) : (
              <Button
                type="primary"
                block
                size="large"
                icon={capturing ? <LoadingOutlined /> : <VideoCameraOutlined />}
                onClick={handleCaptureFrame}
                loading={capturing}
                className="capture-frame-btn"
              >
                {capturing ? '正在抓帧...' : '抓取当前帧'}
              </Button>
            )}
          </div>
        )}

        {testImage && (
          <div className="test-section">
            <div className="image-preview">
              <Image src={testImage} alt="测试图片" preview={false} />
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={handleClearMedia}
                className="clear-btn"
              >
                清除
              </Button>
            </div>
          </div>
        )}

        {testVideoUrl && testVideoFile && (
          <div className="test-section">
            <div className="image-preview">
              <video
                src={testVideoUrl}
                controls
                preload="metadata"
                style={{ width: '100%', display: 'block' }}
              />
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={handleClearMedia}
                className="clear-btn"
              >
                清除
              </Button>
            </div>
          </div>
        )}

        {canRunTest && (
          <div className="test-section">
            <Button
              type="primary"
              block
              size="large"
              icon={<PlayCircleOutlined />}
              onClick={handleRunTest}
              loading={testing}
              className="run-test-btn"
            >
              运行测试
            </Button>
          </div>
        )}

        {testResult && !testing && (
          <div className="test-section">
            {testResult.success ? (
              <>
                <Alert
                  type="success"
                  message="测试完成"
                  description={testResult.message || '执行成功'}
                  icon={<CheckCircleOutlined />}
                  className="result-alert"
                  action={
                    testResult.nodes?.length ? (
                      <Button
                        type="link"
                        onClick={() => setShowResultModal(true)}
                        style={{ color: '#52c41a' }}
                      >
                        查看流程图
                      </Button>
                    ) : undefined
                  }
                />

                <div className="result-summary">
                  <div className="summary-item">
                    <ClockCircleOutlined style={{ color: '#1890ff' }} />
                    <span className="summary-label">总耗时:</span>
                    <span className="summary-value">{testResult.execution_time || testResult.totalTime || 0}ms</span>
                  </div>
                  <div className="summary-item">
                    <CheckCircleOutlined style={{ color: '#52c41a' }} />
                    <span className="summary-label">执行节点:</span>
                    <span className="summary-value">{testResult.nodes?.length || 0} 个</span>
                  </div>
                  <Button
                    type="default"
                    size="small"
                    onClick={() => navigate('/workflow-test-results')}
                    style={{ marginLeft: 'auto' }}
                  >
                    查看测试结果中心
                  </Button>
                </div>
              </>
            ) : (
              <>
                <Alert
                  type="error"
                  message="测试失败"
                  description={testResult.error}
                  icon={<CloseCircleOutlined />}
                  className="result-alert"
                  action={
                    <Button
                      type="link"
                      onClick={() => navigate('/workflow-test-results')}
                      style={{ color: '#ff4d4f' }}
                    >
                      查看测试结果中心
                    </Button>
                  }
                />
                {testResult.details && (
                  <div className="error-details">
                    <div className="error-title">错误详情:</div>
                    <pre className="error-stack">{testResult.details}</pre>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {!canRunTest && !testResult && (
          <div className="test-empty">
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space direction="vertical" size="small">
                  <span>上传图片/视频或从视频源抓帧后开始测试</span>
                  {canUseVideoSource && (
                    <span style={{ fontSize: 12, color: '#1890ff' }}>
                      点击“抓取当前帧”从工作流视频源获取测试图像
                    </span>
                  )}
                </Space>
              }
            />
          </div>
        )}
      </div>

      <TestResultModal
        visible={showResultModal}
        onClose={() => setShowResultModal(false)}
        nodes={nodes}
        edges={edges}
        testResult={testResult}
      />
    </div>
  );
};

export default TestPanel;
