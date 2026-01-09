import React, { useState, useEffect } from 'react';
import {
  Modal,
  Form,
  Input,
  InputNumber,
  Switch,
  Select,
  Button,
  Divider,
  Space,
  message,
} from 'antd';
import {
  VideoCameraOutlined,
  ScanOutlined,
  InfoCircleOutlined,
  SettingOutlined,
  ControlOutlined,
} from '@ant-design/icons';
import { detectStreamInfo } from '@/services/api';
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

  const isEdit = !!editingSource;

  // 当弹窗打开或 editingSource 变化时，回填表单数据
  useEffect(() => {
    if (visible && editingSource) {
      console.log('📝 回填编辑数据:', editingSource);
      // 使用 setTimeout 确保弹窗已打开
      setTimeout(() => {
        form.setFieldsValue({
          source_code: editingSource.source_code,
          name: editingSource.name,
          source_url: editingSource.source_url,
          source_decode_width: editingSource.source_decode_width,
          source_decode_height: editingSource.source_decode_height,
          source_fps: editingSource.source_fps,
          enabled: editingSource.enabled !== undefined ? editingSource.enabled : true,
          status: editingSource.status || 'STOPPED',
        });
      }, 0);
    } else if (visible && !editingSource) {
      // 新增模式，重置表单为初始值
      console.log('📝 重置为新增模式');
      form.resetFields();
      setStreamInfo(null);
    }
  }, [visible, editingSource, form]);

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
          resolution: `${result.width}x${result.height}`,
          fps: result.fps,
        });
        message.success({
          content: '流信息检测成功',
          duration: 2,
        });

        // 提示是否自动填充
        Modal.confirm({
          title: '检测成功',
          content: `检测到流信息：${result.width}x${result.height} @ ${result.fps}fps，是否自动填充解码配置？`,
          onOk: () => {
            form.setFieldsValue({
              source_decode_width: result.width,
              source_decode_height: result.height,
              source_fps: Math.min(Math.max(result.fps, 1), 30),
            });
          },
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
    try {
      const values = await form.validateFields();
      await onSubmit(values);
      form.resetFields();
      setStreamInfo(null);
    } catch (error) {
      // Validation failed
    }
  };

  const handleCancel = () => {
    form.resetFields();
    setStreamInfo(null);
    onCancel();
  };

  return (
    <Modal
      title={
        <div className="source-form-title">
          <div className="title-icon">
            <VideoCameraOutlined />
          </div>
          <span>{isEdit ? '编辑视频源' : '添加视频源'}</span>
        </div>
      }
      open={visible}
      onCancel={handleCancel}
      onOk={handleSubmit}
      width={720}
      okText="保存"
      cancelText="取消"
      className="source-form-modal"
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          enabled: true,
          status: 'STOPPED',
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
              label="源地址"
              name="source_url"
              rules={[{ required: true, message: '请输入源地址' }]}
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

            {streamInfo && (
              <div className="stream-info">
                <InfoCircleOutlined style={{ color: '#1890ff', marginRight: 8 }} />
                <span className="stream-info-label">流信息：</span>
                <span className="stream-info-value">{streamInfo.resolution}</span>
                <span className="stream-info-divider">|</span>
                <span className="stream-info-value">{streamInfo.fps} FPS</span>
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
                label="状态"
                name="status"
                rules={[{ required: true, message: '请选择状态' }]}
              >
                <Select>
                  <Option value="STOPPED">停止</Option>
                  <Option value="RUNNING">运行中</Option>
                  <Option value="ERROR">错误</Option>
                </Select>
              </Form.Item>
            </div>
          </div>
        </div>
      </Form>
    </Modal>
  );
};

export default SourceForm;
