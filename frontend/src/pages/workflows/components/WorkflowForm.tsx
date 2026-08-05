import React, { useEffect, useState } from 'react';
import { Alert, Form, Input, Radio } from 'antd';
import Button from '@/components/common/AppButton';
import AppModal from '@/components/common/AppModal';
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
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (visible) {
      if (editingWorkflow) {
        form.setFieldsValue({
          name: editingWorkflow.name,
          description: editingWorkflow.description || '',
          is_template: editingWorkflow.is_template || false,
        });
      } else {
        form.resetFields();
        form.setFieldValue('is_template', false);
      }
    }
  }, [visible, editingWorkflow, form]);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const values = await form.validateFields();
      await onSubmit(values);
      form.resetFields();
    } catch (error) {
      // 表单验证失败
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = () => {
    form.resetFields();
    onCancel();
  };

  return (
    <AppModal
      title={editingWorkflow ? '编辑算法编排' : '新建算法编排'}
      description="定义编排名称和用途说明"
      open={visible}
      onCancel={handleCancel}
      footer={
        <div className="workflow-form-footer">
          <Button onClick={handleCancel} disabled={submitting}>取消</Button>
          <Button type="primary" onClick={handleSubmit} loading={submitting}>
            {editingWorkflow ? '保存' : '创建并编辑'}
          </Button>
        </div>
      }
      size="md"
      className="workflow-form-modal"
      closable={!submitting}
      keyboard={!submitting}
    >
      <Form
        form={form}
        layout="vertical"
        className="workflow-form"
      >
        {editingWorkflow ? (
          <Alert
            type={editingWorkflow.is_template ? 'info' : 'success'}
            showIcon
            message={editingWorkflow.is_template ? '编排模板' : '普通编排'}
            description="编排类型创建后不可修改"
            className="workflow-type-alert"
          />
        ) : (
          <Form.Item
            label="编排类型"
            name="is_template"
            rules={[{ required: true, message: '请选择编排类型' }]}
          >
            <Radio.Group className="workflow-type-options">
              <Radio.Button value={false}>普通编排</Radio.Button>
              <Radio.Button value={true}>编排模板</Radio.Button>
            </Radio.Group>
          </Form.Item>
        )}

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
    </AppModal>
  );
};

export default WorkflowForm;
