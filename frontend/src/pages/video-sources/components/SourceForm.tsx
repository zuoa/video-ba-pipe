import React, { useState, useEffect } from 'react';
import {
  Form,
  Input,
  InputNumber,
  Switch,
  Select,
  Button,
  Divider,
  Space,
  message,
  Upload,
  List,
  Tag,
  Progress,
} from 'antd';
import {
  VideoCameraOutlined,
  ScanOutlined,
  InfoCircleOutlined,
  SettingOutlined,
  ControlOutlined,
  UploadOutlined,
  FileOutlined,
  DeleteOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { detectStreamInfo, uploadVideoFile, getVideoFiles, deleteVideoFile } from '@/services/api';
import { AppModal, useAppConfirm } from '@/components/common';
import './SourceForm.css';

const { Option } = Select;

export interface SourceFormProps {
  visible: boolean;
  editingSource: any;
  onCancel: () => void;
  onSubmit: (values: any) => Promise<void>;
}

const SourceForm: React.FC<SourceFormProps> = ({
  visible,
  editingSource,
  onCancel,
  onSubmit,
}) => {
  const [form] = Form.useForm();
  const [detecting, setDetecting] = useState(false);
  const [streamInfo, setStreamInfo] = useState<any>(null);
  const [sourceType, setSourceType] = useState<'rtsp' | 'file'>('rtsp');
  const [videoFiles, setVideoFiles] = useState<any[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [saving, setSaving] = useState(false);
  const confirmAction = useAppConfirm();

  const isEdit = !!editingSource;

  // 当弹窗打开或 editingSource 变化时，回填表单数据
  useEffect(() => {
    if (visible && editingSource) {
      // 根据URL判断源类型
      const url = editingSource.source_url || '';
      const type = url.startsWith('rtsp://') || url.startsWith('rtsps://') ? 'rtsp' : 'file';
      setSourceType(type);

      form.setFieldsValue({
        source_code: editingSource.source_code,
        name: editingSource.name,
        source_url: editingSource.source_url,
        source_decode_width: editingSource.source_decode_width,
        source_decode_height: editingSource.source_decode_height,
        source_fps: editingSource.source_fps,
        enabled: editingSource.enabled !== undefined ? editingSource.enabled : true,
        source_type: type,
      });

      // 如果是文件类型，加载文件列表
      if (type === 'file') {
        loadVideoFiles();
      }
    } else if (visible && !editingSource) {
      // 新增模式，重置表单为初始值
      form.resetFields();
      setStreamInfo(null);
      setSourceType('rtsp');
      setVideoFiles([]);
    }
  }, [visible, editingSource, form]);

  // 当源类型切换时加载视频文件列表
  useEffect(() => {
    if (visible && sourceType === 'file') {
      loadVideoFiles();
    }
  }, [sourceType, visible]);

  // 加载视频文件列表
  const loadVideoFiles = async () => {
    setLoadingFiles(true);
    try {
      const result = await getVideoFiles();
      if (result.success) {
        setVideoFiles(result.data || []);
      }
    } catch (error: any) {
      console.error('加载视频文件列表失败:', error);
    } finally {
      setLoadingFiles(false);
    }
  };

  const handleDetect = async () => {
    const url = form.getFieldValue('source_url');
    if (!url) {
      message.warning('请先输入源地址');
      return;
    }

    setDetecting(true);
    try {
      const result = await detectStreamInfo(url);
      if (result.success) {
        setStreamInfo({
          width: result.width,
          height: result.height,
          resolution: `${result.width}x${result.height}`,
          fps: result.fps,
        });
        message.success({
          content: '流信息检测成功',
          duration: 2,
        });
      } else {
        message.error(result.error || '检测失败');
        setStreamInfo(null);
      }
    } catch (error: any) {
      message.error(error?.response?.data?.error || '检测失败');
      setStreamInfo(null);
    } finally {
      setDetecting(false);
    }
  };

  const handleSubmit = async () => {
    setSaving(true);
    try {
      const values = await form.validateFields();
      await onSubmit(values);
      form.resetFields();
      setStreamInfo(null);
    } catch (error) {
      // Validation failed
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    form.resetFields();
    setStreamInfo(null);
    setVideoFiles([]);
    setSourceType('rtsp');
    onCancel();
  };

  // 文件上传处理
  const handleUpload: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options;
    setUploading(true);
    setUploadProgress(0);

    try {
      const result = await uploadVideoFile(file as File);
      if (result.success) {
        message.success('视频上传成功');
        setUploadProgress(100);
        // 自动填充路径
        form.setFieldValue('source_url', result.data.path);
        // 重新加载文件列表
        await loadVideoFiles();
        onSuccess?.(result);
      } else {
        message.error(result.error || '上传失败');
        onError?.(new Error(result.error || '上传失败'));
      }
    } catch (error: any) {
      message.error(error.message || '上传失败');
      onError?.(error);
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  // 删除视频文件
  const handleDeleteFile = async (filename: string) => {
    confirmAction({
      title: '删除视频文件',
      objectName: filename,
      description: '正在使用该文件的视频源可能无法继续运行。',
      onConfirm: async () => {
        try {
          const result = await deleteVideoFile(filename);
          if (result.success) {
            message.success('视频文件已删除');
            await loadVideoFiles();
          } else {
            message.error(result.error || '删除失败');
          }
        } catch (error: any) {
          message.error(error.message || '删除失败');
        }
      },
    });
  };

  // 格式化文件大小
  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
  };

  // 源类型切换处理
  const handleSourceTypeChange = (value: 'rtsp' | 'file') => {
    setSourceType(value);
    setStreamInfo(null);
    // 清空 URL
    form.setFieldValue('source_url', undefined);
  };

  return (
    <AppModal
      title={isEdit ? '编辑视频源' : '添加视频源'}
      description="配置输入地址、解码参数和调度状态"
      open={visible}
      onCancel={handleCancel}
      onOk={handleSubmit}
      size="lg"
      okText={isEdit ? '保存视频源' : '添加视频源'}
      cancelText="取消"
      confirmLoading={saving}
      okButtonProps={{ disabled: saving }}
      cancelButtonProps={{ disabled: saving }}
      closable={!saving && !uploading}
      keyboard={!saving && !uploading}
      className="source-form-modal"
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          enabled: true,
          source_decode_width: 960,
          source_decode_height: 540,
          source_fps: 10,
        }}
      >
        {/* 基本信息 */}
        <div className="form-section">
          <div className="form-section-header">
            <InfoCircleOutlined className="section-icon" />
            <span className="section-title">基本信息</span>
          </div>

          <div className="form-section-content">
            <Form.Item
              label="视频源编码"
              name="source_code"
              rules={[{ required: true, message: '请输入视频源编码' }]}
              extra="唯一标识符，例如: cam001"
            >
              <Input placeholder="例如: cam001" />
            </Form.Item>

            <Form.Item
              label="视频源名称"
              name="name"
              rules={[{ required: true, message: '请输入视频源名称' }]}
              extra="显示名称，例如: 一号摄像头"
            >
              <Input placeholder="例如: 一号摄像头" />
            </Form.Item>
          </div>
        </div>

        {/* 视频源配置 */}
        <div className="form-section">
          <div className="form-section-header">
            <VideoCameraOutlined className="section-icon" />
            <span className="section-title">视频源配置</span>
          </div>

          <div className="form-section-content">
            <Form.Item
              label="源类型"
              name="source_type"
              initialValue="rtsp"
              rules={[{ required: true, message: '请选择源类型' }]}
            >
              <Select onChange={handleSourceTypeChange}>
                <Option value="rtsp">RTSP 流</Option>
                <Option value="file">本地文件</Option>
              </Select>
            </Form.Item>

            {sourceType === 'rtsp' && (
              <Form.Item
                label="RTSP 地址"
                name="source_url"
                rules={[{ required: true, message: '请输入RTSP地址' }]}
              >
                <Input
                  placeholder="rtsp://192.168.1.100:554/stream"
                  addonAfter={
                    <Button
                      type="link"
                      size="small"
                      icon={<ScanOutlined />}
                      onClick={handleDetect}
                      loading={detecting}
                      style={{ padding: '0 8px' }}
                    >
                      检测
                    </Button>
                  }
                />
              </Form.Item>
            )}

            {sourceType === 'file' && (
              <>
                <Form.Item
                  label="视频文件"
                  required
                  extra="上传新文件或从列表中选择已有文件"
                >
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Upload
                      customRequest={handleUpload}
                      showUploadList={false}
                      accept="video/*"
                      disabled={uploading}
                    >
                      <Button icon={<UploadOutlined />} loading={uploading}>
                        {uploading ? '上传中...' : '上传视频文件'}
                      </Button>
                    </Upload>
                    {uploading && uploadProgress > 0 && (
                      <Progress percent={uploadProgress} size="small" />
                    )}
                  </Space>
                </Form.Item>

                {videoFiles.length > 0 && (
                  <Form.Item label="或选择已有文件">
                    <div style={{ border: '1px solid #d9d9d9', borderRadius: '6px', maxHeight: '200px', overflowY: 'auto' }}>
                      <List
                        size="small"
                        loading={loadingFiles}
                        dataSource={videoFiles}
                        renderItem={(item: any) => (
                          <List.Item
                            style={{
                              cursor: 'pointer',
                              padding: '8px 12px',
                              borderBottom: '1px solid #f0f0f0',
                              backgroundColor: form.getFieldValue('source_url') === item.path ? '#e6f7ff' : 'transparent'
                            }}
                            onClick={() => form.setFieldValue('source_url', item.path)}
                            actions={[
                              <Button
                                type="text"
                                size="small"
                                danger
                                icon={<DeleteOutlined />}
                                aria-label={`删除视频文件 ${item.filename}`}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleDeleteFile(item.filename);
                                }}
                              />
                            ]}
                          >
                            <List.Item.Meta
                              avatar={<FileOutlined style={{ fontSize: '20px', color: '#1890ff' }} />}
                              title={
                                <Space>
                                  <span style={{ fontSize: '13px' }}>{item.filename}</span>
                                  {form.getFieldValue('source_url') === item.path && (
                                    <Tag color="blue">已选择</Tag>
                                  )}
                                </Space>
                              }
                              description={<span style={{ fontSize: '12px' }}>{formatFileSize(item.size)}</span>}
                            />
                          </List.Item>
                        )}
                      />
                    </div>
                  </Form.Item>
                )}

                <Form.Item
                  label="文件路径"
                  name="source_url"
                  rules={[{ required: true, message: '请选择或上传视频文件' }]}
                  hidden
                >
                  <Input />
                </Form.Item>
              </>
            )}

            {streamInfo && (
              <div className="stream-info">
                <InfoCircleOutlined style={{ color: '#1890ff', marginRight: 8 }} />
                <div className="stream-info-content">
                  <span className="stream-info-label">检测到流信息</span>
                  <span className="stream-info-value">
                    {streamInfo.resolution} · {streamInfo.fps} FPS
                  </span>
                </div>
                <Button
                  size="small"
                  onClick={() => form.setFieldsValue({
                    source_decode_width: streamInfo.width,
                    source_decode_height: streamInfo.height,
                    source_fps: Math.min(Math.max(streamInfo.fps, 1), 30),
                  })}
                >
                  应用检测参数
                </Button>
              </div>
            )}

            <Divider orientation="left" plain>
              <Space>
                <SettingOutlined />
                解码参数
              </Space>
            </Divider>

            <div className="decode-params">
              <Form.Item
                label="解码宽度"
                name="source_decode_width"
                rules={[{ required: true, message: '请输入解码宽度' }]}
                extra="px"
              >
                <InputNumber min={160} max={4096} style={{ width: '100%' }} />
              </Form.Item>

              <Form.Item
                label="解码高度"
                name="source_decode_height"
                rules={[{ required: true, message: '请输入解码高度' }]}
                extra="px"
              >
                <InputNumber min={90} max={2160} style={{ width: '100%' }} />
              </Form.Item>

              <Form.Item
                label="解码帧率"
                name="source_fps"
                rules={[{ required: true, message: '请输入解码帧率' }]}
                extra="fps"
              >
                <InputNumber min={1} max={60} style={{ width: '100%' }} />
              </Form.Item>
            </div>

            <div className="form-tips">
              <InfoCircleOutlined className="tips-icon" />
              <div className="tips-content">
                <div className="tips-title">提示：</div>
                <div className="tips-list">
                  <div>• 点击"检测"按钮可自动获取流信息</div>
                  <div>• 较低的分辨率和帧率能提高处理效率</div>
                  <div>• 常用预设: 960x540@10fps (推荐) | 1920x1080@8fps | 640x360@15fps</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* 控制 */}
        <div className="form-section">
          <div className="form-section-header">
            <ControlOutlined className="section-icon" />
            <span className="section-title">控制</span>
          </div>

          <div className="form-section-content">
            <div className="control-items">
              <Form.Item
                label="启用"
                name="enabled"
                valuePropName="checked"
                extra="是否激活"
              >
                <Switch />
              </Form.Item>

              <Form.Item
                label="运行状态"
                extra="运行状态由轮转调度器自动维护；“启用”仅表示允许该视频源参与调度。"
              >
                <Tag color={editingSource?.status === 'RUNNING' ? 'green' : 'default'}>
                  {editingSource?.status || 'STOPPED'}
                </Tag>
              </Form.Item>
            </div>
          </div>
        </div>
      </Form>
    </AppModal>
  );
};

export default SourceForm;
