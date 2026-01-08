import React, { useState } from 'react';
import { Modal, Form, Input, Select, Row, Col, message, Upload } from 'antd';
import { UploadOutlined, InboxOutlined } from '@ant-design/icons';
import { uploadModel } from '@/services/api';
import type { UploadFile } from 'antd/es/upload/interface';

const { TextArea } = Input;
const { Dragger } = Upload;

interface UploadModalProps {
  visible: boolean;
  onCancel: () => void;
  onSuccess: () => void;
}

const modelTypes = ['YOLO', 'ONNX', 'TensorRT', 'PyTorch', 'TFLite', 'Custom'];
const frameworks = ['ultralytics', 'onnx', 'tensorrt', 'pytorch', 'tflite', 'custom'];

const UploadModal: React.FC<UploadModalProps> = ({ visible, onCancel, onSuccess }) => {
  const [form] = Form.useForm();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);

  const handleOk = async () => {
    try {
      const values = await form.validateFields();

      if (fileList.length === 0) {
        message.error('请选择模型文件');
        return;
      }

      // 获取真实的 File 对象
      const file = fileList[0].originFileObj || fileList[0];
      if (!file || !(file instanceof File)) {
        message.error('文件对象无效，请重新选择文件');
        return;
      }

      setUploading(true);

      // 一次性上传文件并创建模型记录
      await uploadModel(file as File, {
        name: values.name,
        model_type: values.model_type,
        framework: values.framework,
        version: values.version,
        input_shape: values.input_shape,
        description: values.description,
      });

      message.success('模型上传成功');

      form.resetFields();
      setFileList([]);
      onSuccess();
    } catch (error: any) {
      message.error(error?.message || error?.response?.data?.error || '上传失败');
    } finally {
      setUploading(false);
    }
  };

  const handleCancel = () => {
    form.resetFields();
    setFileList([]);
    onCancel();
  };

  const uploadProps = {
    name: 'file',
    multiple: false,
    fileList,
    beforeUpload: (file: File) => {
      const validExtensions = ['.pt', '.pth', '.onnx', '.engine', '.bin', '.tflite', '.xml', '.param', '.json'];
      const isValid = validExtensions.some(ext => file.name.endsWith(ext));

      if (!isValid) {
        message.error('只支持 .pt, .onnx, .engine 等模型文件格式');
        return Upload.LIST_IGNORE;
      }

      setFileList([file]);
      return false;
    },
    onRemove: () => {
      setFileList([]);
    },
  };

  return (
    <Modal
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div
            style={{
              width: 40,
              height: 40,
              background: 'linear-gradient(135deg, #000000 0%, #333333 100%)',
              borderRadius: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
            }}
          >
            <UploadOutlined />
          </div>
          <span>上传模型</span>
        </div>
      }
      open={visible}
      onCancel={handleCancel}
      onOk={handleOk}
      confirmLoading={uploading}
      width={640}
      okText="上传"
      cancelText="取消"
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item
              label="模型名称"
              name="name"
              rules={[{ required: true, message: '请输入模型名称' }]}
            >
              <Input placeholder="例如: YOLOv8n Person" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label="版本" name="version" initialValue="v1.0">
              <Input />
            </Form.Item>
          </Col>
        </Row>

        <Row gutter={16}>
          <Col span={12}>
            <Form.Item
              label="模型类型"
              name="model_type"
              rules={[{ required: true, message: '请选择模型类型' }]}
            >
              <Select placeholder="选择类型">
                {modelTypes.map((type) => (
                  <Select.Option key={type} value={type}>
                    {type}
                  </Select.Option>
                ))}
              </Select>
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              label="框架"
              name="framework"
              rules={[{ required: true, message: '请选择框架' }]}
            >
              <Select placeholder="选择框架">
                {frameworks.map((fw) => (
                  <Select.Option key={fw} value={fw}>
                    {fw}
                  </Select.Option>
                ))}
              </Select>
            </Form.Item>
          </Col>
        </Row>

        <Form.Item label="输入尺寸" name="input_shape">
          <Input placeholder="例如: 640x640" />
        </Form.Item>

        <Form.Item label="描述" name="description">
          <TextArea rows={3} placeholder="模型功能描述、适用场景等..." />
        </Form.Item>

        <Form.Item label="模型文件" required>
          <Dragger {...uploadProps} style={{ padding: '20px 0' }}>
            <p className="ant-upload-drag-icon">
              <InboxOutlined style={{ fontSize: 48, color: '#d9d9d9' }} />
            </p>
            <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
            <p className="ant-upload-hint">支持 .pt, .onnx, .engine 等格式</p>
          </Dragger>
        </Form.Item>

        <Form.Item name="enabled" valuePropName="checked" initialValue={true} hidden>
          <Input />
        </Form.Item>
      </Form>
    </Modal>
  );
};

export default UploadModal;
