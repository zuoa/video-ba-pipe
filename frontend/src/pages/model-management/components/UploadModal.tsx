import React, { useState } from 'react';
import { Modal, Form, Input, Select, Row, Col, message, Upload, Radio } from 'antd';
import { UploadOutlined, InboxOutlined } from '@ant-design/icons';
import { uploadModel, importModelFromSource } from '@/services/api';
import type { UploadFile } from 'antd/es/upload/interface';

const { TextArea } = Input;
const { Dragger } = Upload;

interface UploadModalProps {
  visible: boolean;
  onCancel: () => void;
  onSuccess: () => void;
}

const modelTypes = ['YOLO', 'RKNN', 'ONNX', 'TensorRT', 'PyTorch', 'TFLite', 'Custom'];
const frameworks = ['ultralytics', 'rknn', 'onnx', 'tensorrt', 'pytorch', 'tflite', 'custom'];

const extensionModelHints: Record<string, { model_type: string; framework: string }> = {
  '.pt': { model_type: 'YOLO', framework: 'ultralytics' },
  '.pth': { model_type: 'PyTorch', framework: 'pytorch' },
  '.onnx': { model_type: 'ONNX', framework: 'onnx' },
  '.engine': { model_type: 'TensorRT', framework: 'tensorrt' },
  '.tflite': { model_type: 'TFLite', framework: 'tflite' },
  '.rknn': { model_type: 'RKNN', framework: 'rknn' },
};

const UploadModal: React.FC<UploadModalProps> = ({ visible, onCancel, onSuccess }) => {
  const [form] = Form.useForm();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const sourceType = Form.useWatch('source_type', form) || 'local';

  const handleOk = async () => {
    try {
      const values = await form.validateFields();

      setUploading(true);

      if (values.source_type === 'local') {
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

        await uploadModel(file as File, {
          name: values.name,
          model_type: values.model_type,
          framework: values.framework,
          version: values.version,
          input_shape: values.input_shape,
          description: values.description,
        });

        message.success('模型上传成功');
      } else if (values.source_type === 'url') {
        await importModelFromSource({
          source_type: 'url',
          name: values.name,
          model_type: values.model_type,
          framework: values.framework,
          version: values.version,
          input_shape: values.input_shape,
          description: values.description,
          source_url: values.source_url,
        });
        message.success('模型拉取成功');
      } else {
        await importModelFromSource({
          source_type: 'huggingface',
          name: values.name,
          model_type: values.model_type,
          framework: values.framework,
          version: values.version,
          input_shape: values.input_shape,
          description: values.description,
          repo_id: values.repo_id,
          filename: values.repo_filename,
          revision: values.revision,
          hf_token: values.hf_token,
        });
        message.success('Hugging Face 模型拉取成功');
      }

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
      const validExtensions = ['.pt', '.pth', '.onnx', '.engine', '.bin', '.tflite', '.xml', '.param', '.json', '.rknn'];
      const lowerFileName = file.name.toLowerCase();
      const isValid = validExtensions.some(ext => lowerFileName.endsWith(ext));

      if (!isValid) {
        message.error('只支持 .pt, .onnx, .engine, .rknn 等模型文件格式');
        return Upload.LIST_IGNORE;
      }

      setFileList([file]);
      const matchedExt = Object.keys(extensionModelHints).find(ext => lowerFileName.endsWith(ext));
      if (matchedExt) {
        form.setFieldsValue(extensionModelHints[matchedExt]);
      }
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
      okText={sourceType === 'local' ? '上传' : '拉取'}
      cancelText="取消"
    >
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        initialValues={{
          source_type: 'local',
          version: 'v1.0',
          revision: 'main',
          model_type: 'YOLO',
          framework: 'ultralytics',
        }}
        onValuesChange={(changedValues) => {
          if (changedValues.source_type && changedValues.source_type !== 'local') {
            setFileList([]);
          }
        }}
      >
        <Form.Item label="导入方式" name="source_type">
          <Radio.Group optionType="button" buttonStyle="solid">
            <Radio.Button value="local">本地上传</Radio.Button>
            <Radio.Button value="url">URL 拉取</Radio.Button>
            <Radio.Button value="huggingface">Hugging Face</Radio.Button>
          </Radio.Group>
        </Form.Item>

        <Row gutter={16}>
          <Col span={12}>
            <Form.Item
              label="模型名称"
              name="name"
              rules={[{ required: sourceType === 'local', message: '请输入模型名称' }]}
            >
              <Input placeholder="例如: YOLOv8n Person（URL/HF可留空自动取文件名）" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label="版本" name="version">
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

        {sourceType === 'local' && (
          <Form.Item label="模型文件" required>
            <Dragger {...uploadProps} style={{ padding: '20px 0' }}>
              <p className="ant-upload-drag-icon">
                <InboxOutlined style={{ fontSize: 48, color: '#d9d9d9' }} />
              </p>
              <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
              <p className="ant-upload-hint">支持 .pt, .onnx, .engine, .rknn 等格式</p>
            </Dragger>
          </Form.Item>
        )}

        {sourceType === 'url' && (
          <Form.Item
            label="模型直链 URL"
            name="source_url"
            rules={[{ required: true, message: '请输入可下载的模型 URL' }]}
          >
            <Input placeholder="https://example.com/models/model.rknn" />
          </Form.Item>
        )}

        {sourceType === 'huggingface' && (
          <>
            <Form.Item
              label="仓库 ID"
              name="repo_id"
              rules={[{ required: true, message: '请输入仓库ID，例如 user/repo' }]}
            >
              <Input placeholder="例如: PaddlePaddle/PP-YOLOE" />
            </Form.Item>
            <Form.Item
              label="模型文件路径"
              name="repo_filename"
              rules={[{ required: true, message: '请输入仓库内模型文件路径' }]}
            >
              <Input placeholder="例如: model.onnx 或 weights/model.rknn" />
            </Form.Item>
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item label="Revision" name="revision">
                  <Input placeholder="main" />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="HF Token（私有仓库可选）" name="hf_token">
                  <Input.Password placeholder="hf_xxx" />
                </Form.Item>
              </Col>
            </Row>
          </>
        )}

        <Form.Item name="enabled" valuePropName="checked" initialValue={true} hidden>
          <Input />
        </Form.Item>
      </Form>
    </Modal>
  );
};

export default UploadModal;
