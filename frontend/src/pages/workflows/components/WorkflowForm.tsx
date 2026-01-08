import React, { useEffect } from 'react';
import { Modal, Form, Input, Button } from 'antd';
import { ApartmentOutlined } from '@ant-design/icons';
import './WorkflowForm.css';

const { TextArea } = Input;

export interface WorkflowFormProps {
  visible: boolean;
  editingWorkflow: any;
  onCancel: () => void;
  onSubmit: (values: any) => void;
}

const WorkflowForm: React.FC<WorkflowFormProps> = ({
  visible,
  editingWorkflow,
  onCancel,
  onSubmit,
}) => {
  const [form] = Form.useForm();

  useEffect(() => {
    if (visible) {
      if (editingWorkflow) {
        form.setFieldsValue({
          name: editingWorkflow.name,
          description: editingWorkflow.description || '',
        });
      } else {
        form.resetFields();
      }
    }
  }, [visible, editingWorkflow, form]);

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      await onSubmit(values);
      form.resetFields();
    } catch (error) {
      // 表单验证失败
    }
  };

  return (
    <Modal
      title={
        <div className="modal-title">
          <ApartmentOutlined className="modal-title-icon" />
          <span>{editingWorkflow ? '编辑算法编排' : '新建算法编排'}</span>
        </div>
      }
      open={visible}
      onCancel={() => {
        form.resetFields();
        onCancel();
      }}
      footer={
        <div className="modal-footer">
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" onClick={handleSubmit}>
            {editingWorkflow ? '保存' : '创建并编辑'}
          </Button>
        </div>
      }
      width={600}
      className="workflow-form-modal"
    >
      <Form
        form={form}
        layout="vertical"
        className="workflow-form"
      >
        <Form.Item
          label="算法编排名称"
          name="name"
          rules={[{ required: true, message: '请输入算法编排名称' }]}
        >
          <Input
            placeholder="例如: 门口监控算法编排"
            size="large"
          />
        </Form.Item>

        <Form.Item
          label="描述"
          name="description"
        >
          <TextArea
            rows={4}
            placeholder="描述算法编排的用途（视频源请在编排编辑器中配置）"
          />
        </Form.Item>
      </Form>
    </Modal>
  );
};

export default WorkflowForm;
