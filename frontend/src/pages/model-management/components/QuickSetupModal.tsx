import React, { useEffect, useState } from 'react';
import { Alert, Form, Input, Spin, Tag } from 'antd';
import {
  ApartmentOutlined,
  BellOutlined,
  BugOutlined,
  CheckCircleFilled,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { useNavigate } from '@umijs/max';
import AppButton from '@/components/common/AppButton';
import AppModal from '@/components/common/AppModal';
import {
  createModelQuickSetup,
  getModelQuickSetup,
  type ModelQuickSetupPreview,
  type ModelQuickSetupResult,
} from '@/services/api';
import './QuickSetupModal.css';

interface QuickSetupModel {
  id: number;
  name: string;
}

interface QuickSetupModalProps {
  visible: boolean;
  model: QuickSetupModel | null;
  onClose: () => void;
  onCreated: () => void | Promise<void>;
}

const QuickSetupModal: React.FC<QuickSetupModalProps> = ({
  visible,
  model,
  onClose,
  onCreated,
}) => {
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [preview, setPreview] = useState<ModelQuickSetupPreview | null>(null);
  const [result, setResult] = useState<ModelQuickSetupResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible || !model) return undefined;

    let active = true;
    setLoading(true);
    setPreview(null);
    setResult(null);
    setError('');
    form.resetFields();

    getModelQuickSetup(model.id)
      .then((data) => {
        if (!active) return;
        setPreview(data);
        form.setFieldsValue({
          algorithm_name: data.existing.algorithm?.name || data.defaults.algorithm_name,
          template_name: data.existing.workflow_template?.name || data.defaults.template_name,
        });
      })
      .catch((requestError: any) => {
        if (!active) return;
        setError(requestError?.data?.error || requestError?.message || '无法加载快速创建配置');
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [form, model, visible]);

  const handleCreate = async () => {
    if (!model) return;
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      setError('');
      const created = await createModelQuickSetup(model.id, values);
      setResult(created);
      await onCreated();
    } catch (requestError: any) {
      if (requestError?.errorFields) return;
      setError(requestError?.data?.error || requestError?.message || '快速创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  const goToAlgorithm = () => {
    if (!result) return;
    onClose();
    navigate(`/algorithms/wizard?edit=${result.algorithm.id}`);
  };

  const goToTemplate = () => {
    if (!result) return;
    onClose();
    navigate(`/workflows/editor/${result.workflow_template.id}`);
  };

  const goToWizard = () => {
    onClose();
    navigate('/algorithms/wizard');
  };

  const footer = result ? (
    <div className="quick-setup-footer">
      <AppButton onClick={onClose}>关闭</AppButton>
      <div className="quick-setup-footer__actions">
        <AppButton icon={<BugOutlined />} onClick={goToAlgorithm}>查看算法</AppButton>
        <AppButton type="primary" icon={<ApartmentOutlined />} onClick={goToTemplate}>
          编辑模板
        </AppButton>
      </div>
    </div>
  ) : preview && !preview.eligible ? (
    <div className="quick-setup-footer">
      <AppButton onClick={onClose}>关闭</AppButton>
      <AppButton type="primary" onClick={goToWizard}>打开完整向导</AppButton>
    </div>
  ) : (
    <div className="quick-setup-footer">
      <AppButton onClick={onClose} disabled={submitting}>取消</AppButton>
      <AppButton
        type="primary"
        icon={<ApartmentOutlined />}
        loading={submitting}
        disabled={loading || !preview?.eligible}
        onClick={handleCreate}
      >
        创建算法和模板
      </AppButton>
    </div>
  );

  return (
    <AppModal
      title="从模型快速创建"
      description={model ? `为「${model.name}」生成可复制的告警编排` : undefined}
      open={visible}
      onCancel={onClose}
      footer={footer}
      size="md"
      className="quick-setup-modal"
      closable={!submitting}
      keyboard={!submitting}
    >
      {loading ? (
        <div className="quick-setup-loading"><Spin tip="正在读取通用脚本配置…" /></div>
      ) : result ? (
        <div className="quick-setup-success">
          <CheckCircleFilled className="quick-setup-success__icon" />
          <div>
            <h3>{result.message}</h3>
            <p>算法与模板已经关联，可以直接编辑模板或继续处理其他模型。</p>
          </div>
          <div className="quick-setup-result-grid">
            <div>
              <span>算法</span>
              <strong>{result.algorithm.name}</strong>
              <Tag color={result.algorithm.created ? 'green' : 'blue'}>
                {result.algorithm.created ? '已创建' : '已复用'}
              </Tag>
            </div>
            <div>
              <span>编排模板</span>
              <strong>{result.workflow_template.name}</strong>
              <Tag color={result.workflow_template.created ? 'green' : 'blue'}>
                {result.workflow_template.created ? '已创建' : '已复用'}
              </Tag>
            </div>
          </div>
        </div>
      ) : preview ? (
        <div className="quick-setup-content">
          {!preview.eligible ? (
            <Alert
              type="warning"
              showIcon
              message="该模型不能使用通用检测脚本"
              description={`${preview.reason || '模型配置不兼容'}。你仍可通过完整算法向导选择其他脚本。`}
            />
          ) : (
            <>
              <div className="quick-setup-pipeline" aria-label="视频源占位连接算法节点，再连接告警输出节点">
                <div className="quick-setup-node is-source">
                  <VideoCameraOutlined />
                  <span>视频源占位</span>
                </div>
                <span className="quick-setup-rail" aria-hidden="true">→</span>
                <div className="quick-setup-node is-algorithm">
                  <BugOutlined />
                  <span>算法节点</span>
                </div>
                <span className="quick-setup-rail" aria-hidden="true">→</span>
                <div className="quick-setup-node is-alert">
                  <BellOutlined />
                  <span>告警输出</span>
                </div>
              </div>

              <div className="quick-setup-script">
                <div>
                  <span>通用脚本</span>
                  <strong>{preview.script?.name}</strong>
                </div>
                <code>{preview.script?.path}</code>
                {preview.script?.version ? <Tag>{preview.script.version}</Tag> : null}
              </div>

              {error ? <Alert type="error" showIcon message={error} /> : null}

              <Form form={form} layout="vertical" requiredMark={false}>
                <Form.Item
                  label="算法名称"
                  name="algorithm_name"
                  extra={preview.existing.algorithm ? '检测到已有快速算法，本次将直接复用。' : undefined}
                  rules={[
                    { required: true, whitespace: true, message: '请输入算法名称' },
                  ]}
                >
                  <Input disabled={Boolean(preview.existing.algorithm)} maxLength={120} />
                </Form.Item>
                <Form.Item
                  label="编排模板名称"
                  name="template_name"
                  extra={preview.existing.workflow_template ? '检测到已有编排模板，本次将直接复用。' : undefined}
                  rules={[
                    { required: true, whitespace: true, message: '请输入编排模板名称' },
                  ]}
                >
                  <Input disabled={Boolean(preview.existing.workflow_template)} maxLength={120} />
                </Form.Item>
              </Form>

              <Alert
                type="info"
                showIcon
                message="模板保持未绑定、未激活"
                description="复制模板到具体视频源后再启用；算法和模板会在同一事务中创建。"
              />
            </>
          )}
        </div>
      ) : (
        <Alert type="error" showIcon message={error || '无法加载快速创建配置'} />
      )}
    </AppModal>
  );
};

export default QuickSetupModal;
