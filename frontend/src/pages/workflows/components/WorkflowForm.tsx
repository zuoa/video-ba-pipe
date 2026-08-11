import React, { useEffect, useState } from 'react';
import { Alert, Form, Input, Radio } from 'antd';
import { ApartmentOutlined, FileTextOutlined } from '@ant-design/icons';
import Button from '@/components/common/AppButton';
import AppModal from '@/components/common/AppModal';
import type { Workflow, WorkflowFormValues } from '@/services/api';
import './WorkflowForm.css';

const { TextArea } = Input;

export interface WorkflowFormProps {
  visible: boolean;
  editingWorkflow: Workflow | null;
  onCancel: () => void;
  onSubmit: (values: WorkflowFormValues) => Promise<void>;
}

const WorkflowForm: React.FC<WorkflowFormProps> = ({
  visible,
  editingWorkflow,
  onCancel,
  onSubmit,
}) => {
  const [form] = Form.useForm<WorkflowFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const isTemplate = Form.useWatch('is_template', form) ?? false;

  useEffect(() => {
    if (!visible) return;
    if (editingWorkflow) {
      form.setFieldsValue({
        name: editingWorkflow.name,
        description: editingWorkflow.description || '',
        is_template: editingWorkflow.is_template,
      });
    } else {
      form.resetFields();
      form.setFieldValue('is_template', false);
    }
  }, [visible, editingWorkflow, form]);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const values = await form.validateFields();
      await onSubmit(values);
      form.resetFields();
    } catch (error) {
      // Validation and request errors are rendered by the form or parent handler.
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
      description={editingWorkflow ? '修改名称和用途说明，编排类型保持不变' : '先选择用途，再进入可视化编排编辑器'}
      open={visible}
      onCancel={handleCancel}
      footer={
        <div className="workflow-form-footer">
          <Button onClick={handleCancel} disabled={submitting}>取消</Button>
          <Button type="primary" onClick={handleSubmit} loading={submitting}>
            {editingWorkflow ? '保存修改' : isTemplate ? '创建模板并编排' : '创建编排并配置'}
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
        requiredMark={false}
      >
        {editingWorkflow ? (
          <Alert
            type={editingWorkflow.is_template ? 'info' : 'success'}
            showIcon
            message={editingWorkflow.is_template ? '编排模板' : '运行编排'}
            description={editingWorkflow.is_template
              ? '模板不绑定视频源且不会调度，可应用到多个视频源。编排类型创建后不可修改。'
              : '运行编排可绑定视频源并激活调度。编排类型创建后不可修改。'}
            className="workflow-type-alert"
          />
        ) : (
          <Form.Item
            label="编排类型"
            name="is_template"
            rules={[{ required: true, message: '请选择编排类型' }]}
          >
            <Radio.Group className="workflow-type-options">
              <Radio.Button value={false}>
                <span className="workflow-type-option__icon"><ApartmentOutlined /></span>
                <span className="workflow-type-option__copy">
                  <strong>运行编排</strong>
                  <small>绑定一个视频源，可激活调度</small>
                </span>
              </Radio.Button>
              <Radio.Button value={true}>
                <span className="workflow-type-option__icon"><FileTextOutlined /></span>
                <span className="workflow-type-option__copy">
                  <strong>编排模板</strong>
                  <small>不绑定视频源，用于批量复用</small>
                </span>
              </Radio.Button>
            </Radio.Group>
          </Form.Item>
        )}

        <Form.Item
          label="算法编排名称"
          name="name"
          rules={[{ required: true, whitespace: true, message: '请输入算法编排名称' }]}
        >
          <Input
            placeholder={isTemplate ? '例如：园区人员检测模板' : '例如：东门人员检测编排'}
            size="large"
            maxLength={120}
            showCount
          />
        </Form.Item>

        <Form.Item label="用途说明" name="description">
          <TextArea
            rows={4}
            maxLength={500}
            showCount
            placeholder={isTemplate ? '说明模板适用的检测场景和复用方式' : '说明该编排负责的视频分析任务'}
          />
        </Form.Item>
      </Form>
    </AppModal>
  );
};

export default WorkflowForm;
