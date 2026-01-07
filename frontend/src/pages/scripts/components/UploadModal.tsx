import React, { useState, useEffect } from 'react';
import { Modal, Form, Input, Select, Checkbox, Space, message } from 'antd';
import { UploadOutlined, CodeOutlined } from '@ant-design/icons';
import { createScript } from '@/services/api';
import CodeEditor from './CodeEditor';

const { Option } = Select;
const { TextArea } = Input;

export interface UploadModalProps {
  visible: boolean;
  script?: any;
  onCancel: () => void;
  onSuccess: () => void;
}

const UploadModal: React.FC<UploadModalProps> = ({
  visible,
  script,
  onCancel,
  onSuccess,
}) => {
  const [form] = Form.useForm();
  const [codeContent, setCodeContent] = useState('');
  const [createAlgorithm, setCreateAlgorithm] = useState(false);

  useEffect(() => {
    if (visible) {
      if (script?.content) {
        setCodeContent(script.content);
        if (script.path) {
          const parts = script.path.split('/');
          const category = parts[0] || 'detectors';
          form.setFieldsValue({
            path: script.path,
            category: category,
          });
        }
      } else {
        form.resetFields();
        setCodeContent('');
        setCreateAlgorithm(false);
      }
    }
  }, [visible, script, form]);

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();

      if (!codeContent.trim()) {
        message.error('请输入脚本内容');
        return;
      }

      await createScript({
        path: values.path,
        content: codeContent,
        category: values.category,
        create_algorithm: createAlgorithm,
      });

      onSuccess();
    } catch (error: any) {
      console.error('Upload error:', error);
      if (error?.errorFields) {
        return; // 表单验证错误
      }
      message.error(error?.response?.data?.error || '上传失败');
    }
  };

  const handleCancel = () => {
    form.resetFields();
    setCodeContent('');
    setCreateAlgorithm(false);
    onCancel();
  };

  return (
    <Modal
      title={
        <Space>
          <div className="modal-icon">
            <UploadOutlined />
          </div>
          <span>{script?.content ? '使用模板创建脚本' : '上传脚本'}</span>
        </Space>
      }
      open={visible}
      onCancel={handleCancel}
      onOk={handleSubmit}
      width={800}
      okText="上传"
      cancelText="取消"
      className="upload-modal"
      centered
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          category: 'detectors',
        }}
      >
        <Form.Item
          label="脚本路径"
          name="path"
          rules={[{ required: true, message: '请输入脚本路径' }]}
          help="相对于 app/user_scripts/ 的路径，例如: detectors/my_detector.py"
        >
          <Input
            prefix={<CodeOutlined />}
            placeholder="detectors/my_detector.py"
            size="large"
          />
        </Form.Item>

        <Form.Item
          label="类别"
          name="category"
          rules={[{ required: true, message: '请选择类别' }]}
        >
          <Select size="large">
            <Option value="detectors">检测脚本 (detectors)</Option>
            <Option value="filters">过滤脚本 (filters)</Option>
            <Option value="hooks">Hook脚本 (hooks)</Option>
            <Option value="postprocessors">后处理脚本 (postprocessors)</Option>
          </Select>
        </Form.Item>

        <Form.Item label="脚本内容" required>
          <CodeEditor
            value={codeContent}
            onChange={setCodeContent}
            height={400}
          />
        </Form.Item>

        <Form.Item>
          <Checkbox
            checked={createAlgorithm}
            onChange={(e) => setCreateAlgorithm(e.target.checked)}
          >
            同时创建算法记录
          </Checkbox>
        </Form.Item>
      </Form>
    </Modal>
  );
};

export default UploadModal;
