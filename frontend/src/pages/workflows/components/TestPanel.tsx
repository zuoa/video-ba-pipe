import React, { useState, useEffect, useMemo } from 'react';
import { Upload, Select, Button, Empty, Alert, Space, Spin, Image, Tabs, message, Tag } from 'antd';
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
} from '@ant-design/icons';
import type { UploadFile } from 'antd/es/upload/interface';
import { captureFrame, testWorkflow } from '@/services/api';
import TestResultModal from './TestResultModal';
import './TestPanel.css';

const { Dragger } = Upload;
const { Option } = Select;

export interface TestPanelProps {
  workflow: any;
  nodes?: any[];
  edges?: any[];
  videoSources?: any[];
}

const TestPanel: React.FC<TestPanelProps> = ({ workflow, nodes = [], edges = [], videoSources = [] }) => {
  const [imageSource, setImageSource] = useState<'upload' | 'video'>('video');
  const [testImage, setTestImage] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [showResultModal, setShowResultModal] = useState(false);

  // 查找工作流中的视频源节点
  const workflowVideoSourceNode = useMemo(() => {
    return nodes.find(node =>
      node.type === 'videoSource' || node.data?.type === 'videoSource' || node.data?.type === 'source'
    );
  }, [nodes]);

  // 获取工作流的视频源ID
  const workflowVideoSourceId = useMemo(() => {
    return workflowVideoSourceNode?.data?.videoSourceId;
  }, [workflowVideoSourceNode]);

  // 获取视频源信息
  const videoSourceInfo = useMemo(() => {
    if (!workflowVideoSourceId) return null;
    return videoSources.find(s => s.id == workflowVideoSourceId); // 使用 == 而不是 ===，避免类型不匹配
  }, [workflowVideoSourceId, videoSources]);

  // 判断是否可以使用视频源模式
  const canUseVideoSource = Boolean(workflowVideoSourceId && videoSourceInfo);

  // 调试日志
  useEffect(() => {
    console.log('📹 TestPanel 调试信息:', {
      nodesCount: nodes.length,
      videoSourcesCount: videoSources.length,
      workflowVideoSourceNode: workflowVideoSourceNode?.id,
      workflowVideoSourceId,
      workflowVideoSourceIdType: typeof workflowVideoSourceId,
      videoSourceInfo: videoSourceInfo?.name,
      canUseVideoSource,
      所有节点: nodes.map(n => ({ id: n.id, type: n.type, videoSourceId: n.data?.videoSourceId })),
      所有视频源: videoSources.map(s => ({ id: s.id, name: s.name })),
    });
  }, [nodes, videoSources, workflowVideoSourceNode, workflowVideoSourceId, videoSourceInfo, canUseVideoSource]);

  const handleImageUpload = (file: File) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      setTestImage(e.target?.result as string);
    };
    reader.readAsDataURL(file);
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
        // 直接使用返回的 base64 图片数据
        setTestImage(response.image);
        message.success(`抓帧成功 (${response.resolution || ''})`);
      } else {
        throw new Error('无效的响应数据');
      }
    } catch (error: any) {
      console.error('抓帧失败:', error);
      message.error(error.message || '抓帧失败，请检查视频源连接');
    } finally {
      setCapturing(false);
    }
  };

  const handleRunTest = async () => {
    if (!testImage) {
      message.warning('请先上传测试图片或从视频源抓帧');
      return;
    }

    if (!workflow?.id) {
      message.error('工作流ID不存在');
      return;
    }

    setTesting(true);
    setTestResult(null);

    try {
      // 调用新的工作流测试API
      const response = await testWorkflow(workflow.id, testImage);

      if (response.success || response.nodes) {
        setTestResult(response);
        setShowResultModal(true);
        message.success('测试完成，点击查看详细结果');
      } else {
        setTestResult({
          success: false,
          error: response.error || '测试失败',
          details: response.traceback,
        });
        message.error('测试失败: ' + (response.error || '未知错误'));
      }
    } catch (error: any) {
      console.error('测试失败:', error);
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

  const handleClearImage = () => {
    setTestImage(null);
    setTestResult(null);
  };

  return (
    <div className="test-panel">
      <div className="panel-header">
        <PlayCircleOutlined className="panel-icon" />
        <span className="panel-title">算法编排测试</span>
      </div>

      <div className="test-content">
        {/* 工作流信息提示 */}
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

        {/* 图片来源选择 */}
        <div className="test-section">
          <div className="section-label">图片来源</div>
          <div className="source-tabs">
            <Button
              size="small"
              type={imageSource === 'upload' ? 'primary' : 'default'}
              icon={<UploadOutlined />}
              onClick={() => setImageSource('upload')}
            >
              上传图片
            </Button>
            <Button
              size="small"
              type={imageSource === 'video' ? 'primary' : 'default'}
              icon={<VideoCameraOutlined />}
              onClick={() => setImageSource('video')}
            >
              视频源
            </Button>
          </div>
        </div>

        {/* 上传图片区域 */}
        {imageSource === 'upload' && !testImage && (
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

        {/* 从视频源抓帧 */}
        {imageSource === 'video' && !testImage && (
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

        {/* 图片预览 */}
        {testImage && (
          <div className="test-section">
            <div className="image-preview">
              <Image src={testImage} alt="测试图片" preview={false} />
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={handleClearImage}
                className="clear-btn"
              >
                清除图片
              </Button>
            </div>

            {/* 测试按钮 */}
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

        {/* 测试结果 */}
        {testResult && !testing && (
          <div className="test-section">
            {testResult.success ? (
              <>
                <Alert
                  type="success"
                  message="测试通过"
                  description={testResult.message}
                  icon={<CheckCircleOutlined />}
                  className="result-alert"
                  action={
                    <Button
                      type="link"
                      onClick={() => setShowResultModal(true)}
                      style={{ color: '#52c41a' }}
                    >
                      查看流程图
                    </Button>
                  }
                />

                <div className="result-summary">
                  <div className="summary-item">
                    <ClockCircleOutlined style={{ color: '#1890ff' }} />
                    <span className="summary-label">总耗时:</span>
                    <span className="summary-value">{testResult.execution_time || testResult.totalTime}ms</span>
                  </div>
                  <div className="summary-item">
                    <CheckCircleOutlined style={{ color: '#52c41a' }} />
                    <span className="summary-label">执行节点:</span>
                    <span className="summary-value">{testResult.nodes?.length || 0} 个</span>
                  </div>
                  <Button
                    type="primary"
                    size="small"
                    onClick={() => setShowResultModal(true)}
                    style={{ marginLeft: 'auto' }}
                  >
                    查看详细流程
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
                      onClick={() => setShowResultModal(true)}
                      style={{ color: '#ff4d4f' }}
                    >
                      查看详情
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

        {/* 空状态 */}
        {!testImage && !testResult && (
          <div className="test-empty">
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space direction="vertical" size="small">
                  <span>上传图片或从视频源抓帧后开始测试</span>
                  {canUseVideoSource && (
                    <span style={{ fontSize: 12, color: '#1890ff' }}>
                      点击"抓取当前帧"从工作流视频源获取图片
                    </span>
                  )}
                </Space>
              }
            />
          </div>
        )}
      </div>

      {/* 测试结果弹窗 */}
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
