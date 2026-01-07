import React, { useState, useEffect } from 'react';
import {
  Modal,
  Form,
  Input,
  InputNumber,
  Select,
  Switch,
  Divider,
  Space,
  Alert,
  Button,
  message,
} from 'antd';
import {
  ExperimentOutlined,
  InfoCircleOutlined,
  CodeOutlined,
  ClockCircleOutlined,
  SettingOutlined,
  ApiOutlined,
  FileTextOutlined,
  ExportOutlined,
} from '@ant-design/icons';
import { getDetectorTemplates } from '@/services/api';
import type { Algorithm } from './AlgorithmTable';
import './AlgorithmForm.css';

const { Option } = Select;
const { TextArea } = Input;

export interface AlgorithmFormProps {
  visible: boolean;
  editingAlgorithm: Algorithm | null;
  pluginModules: string[];
  onCancel: () => void;
  onSubmit: (values: any) => Promise<void>;
}

const AlgorithmForm: React.FC<AlgorithmFormProps> = ({
  visible,
  editingAlgorithm,
  pluginModules,
  onCancel,
  onSubmit,
}) => {
  const [form] = Form.useForm();
  const [templates, setTemplates] = useState<any[]>([]);
  const [loadingTemplates, setLoadingTemplates] = useState(false);

  const isEdit = !!editingAlgorithm;
  const isScriptType = Form.useWatch('plugin_module', form) === 'script_algorithm';

  useEffect(() => {
    if (visible) {
      loadTemplates();

      // 回显编辑数据
      if (editingAlgorithm) {
        const formValues = {
          ...editingAlgorithm,
          // 处理 ext_config_json：如果是字符串则解析，否则使用默认空对象
          ext_config_json: typeof editingAlgorithm.ext_config_json === 'string'
            ? editingAlgorithm.ext_config_json
            : JSON.stringify(editingAlgorithm.ext_config_json || {}),
        };
        form.setFieldsValue(formValues);
      } else {
        // 新建时重置表单
        form.resetFields();
      }
    }
  }, [visible, editingAlgorithm, form]);

  const loadTemplates = async () => {
    setLoadingTemplates(true);
    try {
      const data = await getDetectorTemplates();
      setTemplates(data || []);
    } catch (error) {
      console.error('加载模板失败');
    } finally {
      setLoadingTemplates(false);
    }
  };

  const handleTemplateChange = (templateId: number) => {
    const template = templates.find((t) => t.id === templateId);
    if (template) {
      form.setFieldsValue({
        script_path: template.script_path,
        plugin_module: 'script_algorithm',
      });
    }
  };

  const handleWindowCheckChange = (checked: boolean) => {
    if (!checked) {
      form.setFieldsValue({
        window_size: 30,
        window_mode: 'ratio',
        window_threshold: 0.3,
      });
    }
  };

  const handleWindowModeChange = (mode: string) => {
    if (mode === 'ratio') {
      form.setFieldsValue({ window_threshold: 0.3 });
    } else {
      form.setFieldsValue({ window_threshold: 5 });
    }
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      await onSubmit(values);
      form.resetFields();
    } catch (error) {
      // Validation failed or submit failed
    }
  };

  const handleCancel = () => {
    form.resetFields();
    onCancel();
  };

  return (
    <Modal
      title={
        <div className="algorithm-form-title">
          <div className="title-icon">
            <ExperimentOutlined />
          </div>
          <span>{isEdit ? '编辑算法' : '添加算法'}</span>
        </div>
      }
      open={visible}
      onCancel={handleCancel}
      onOk={handleSubmit}
      width={800}
      okText="保存"
      cancelText="取消"
      className="algorithm-form-modal"
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          label_name: 'Object',
          interval_seconds: 1,
          plugin_module: 'script_algorithm',
          entry_function: 'process',
          runtime_timeout: 30,
          memory_limit_mb: 512,
          enable_window_check: false,
          window_size: 30,
          window_mode: 'ratio',
          window_threshold: 0.3,
          ext_config_json: '{}',
        }}
      >
        {/* 基本信息 */}
        <div className="form-section">
          <div className="form-section-header">
            <InfoCircleOutlined className="section-icon" />
            <span className="section-title">基本信息</span>
          </div>

          <div className="form-section-content">
            {!isEdit && (
              <Form.Item label="使用模板（可选）" name="template_id">
                <Select
                  placeholder="选择检测器模板快速创建"
                  allowClear
                  loading={loadingTemplates}
                  onChange={handleTemplateChange}
                >
                  {templates.map((t) => (
                    <Option key={t.id} value={t.id}>
                      <Space>
                        <FileTextOutlined />
                        {t.name} - {t.description}
                      </Space>
                    </Option>
                  ))}
                </Select>
              </Form.Item>
            )}

            <Form.Item
              label="算法名称"
              name="name"
              rules={[{ required: true, message: '请输入算法名称' }]}
            >
              <Input placeholder="例如: 人员检测算法" />
            </Form.Item>

            <div className="form-row">
              <Form.Item
                label="标签名称"
                name="label_name"
                rules={[{ required: true, message: '请输入标签名称' }]}
              >
                <Input placeholder="例如: Person" />
              </Form.Item>

              <Form.Item
                label="检测间隔（秒）"
                name="interval_seconds"
                rules={[{ required: true, message: '请输入检测间隔' }]}
              >
                <InputNumber min={0.1} max={60} step={0.1} style={{ width: '100%' }} />
              </Form.Item>
            </div>
          </div>
        </div>

        {/* 插件配置 */}
        <div className="form-section">
          <div className="form-section-header">
            <ApiOutlined className="section-icon" />
            <span className="section-title">插件配置</span>
          </div>

          <div className="form-section-content">
            <Form.Item
              label="插件模块"
              name="plugin_module"
              rules={[{ required: true, message: '请选择插件模块' }]}
            >
              <Select placeholder="选择插件模块">
                {pluginModules.map((module) => (
                  <Option key={module} value={module}>
                    {module || '(不选择插件)'}
                  </Option>
                ))}
              </Select>
            </Form.Item>

            {isScriptType && (
              <>
                <Alert
                  message="脚本配置"
                  description={
                    <div className="script-alert-content">
                      <p>使用脚本前请确保：</p>
                      <ul>
                        <li>脚本已上传到脚本管理页面</li>
                        <li>脚本包含必需的 SCRIPT_METADATA 和 process 函数</li>
                        <li>脚本已通过语法验证</li>
                      </ul>
                      <div className="script-alert-actions">
                        <Button
                          type="link"
                          icon={<ExportOutlined />}
                          onClick={() => window.open('/scripts/templates', '_blank')}
                          size="small"
                        >
                          查看模板
                        </Button>
                        <Button
                          type="link"
                          icon={<CodeOutlined />}
                          onClick={() => window.open('/scripts', '_blank')}
                          size="small"
                        >
                          管理脚本
                        </Button>
                      </div>
                    </div>
                  }
                  type="info"
                  showIcon
                  className="script-config-alert"
                />

                <Form.Item
                  label="脚本路径"
                  name="script_path"
                  rules={[{ required: true, message: '请输入脚本路径' }]}
                  extra="相对于 app/user_scripts/ 的路径，例如: detectors/my_detector.py"
                >
                  <Input placeholder="detectors/my_detector.py" />
                </Form.Item>

                <div className="form-row">
                  <Form.Item
                    label="入口函数"
                    name="entry_function"
                    extra="默认为 process"
                  >
                    <Input placeholder="process" />
                  </Form.Item>

                  <Form.Item
                    label="运行超时（秒）"
                    name="runtime_timeout"
                    extra="默认 30 秒"
                  >
                    <InputNumber min={1} max={300} style={{ width: '100%' }} />
                  </Form.Item>
                </div>

                <Form.Item
                  label="内存限制（MB）"
                  name="memory_limit_mb"
                  extra="默认 512MB"
                >
                  <InputNumber min={64} max={4096} style={{ width: '100%' }} />
                </Form.Item>
              </>
            )}
          </div>
        </div>

        {/* 时间窗口配置 */}
        <div className="form-section">
          <div className="form-section-header">
            <ClockCircleOutlined className="section-icon" />
            <span className="section-title">时间窗口检测（误报抑制）</span>
          </div>

          <div className="form-section-content">
            <Form.Item
              label="启用时间窗口检测"
              name="enable_window_check"
              valuePropName="checked"
              extra="在时间窗口内多次检测确认后才触发告警，减少误报"
            >
              <Switch onChange={handleWindowCheckChange} />
            </Form.Item>

            <Form.Item noStyle shouldUpdate={(prev, curr) => prev.enable_window_check !== curr.enable_window_check}>
              {({ getFieldValue }) =>
                getFieldValue('enable_window_check') ? (
                  <div className="window-config-fields">
                    <div className="form-row">
                      <Form.Item
                        label="窗口大小（秒）"
                        name="window_size"
                        rules={[{ required: true }]}
                        extra="在此时间内统计检测"
                      >
                        <InputNumber min={5} max={300} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="窗口模式"
                        name="window_mode"
                        rules={[{ required: true }]}
                        extra="判断方式"
                      >
                        <Select onChange={handleWindowModeChange}>
                          <Option value="ratio">占比模式</Option>
                          <Option value="count">次数模式</Option>
                          <Option value="consecutive">连续模式</Option>
                        </Select>
                      </Form.Item>
                    </div>

                    <Form.Item noStyle shouldUpdate={(prev, curr) => prev.window_mode !== curr.window_mode}>
                      {({ getFieldValue }) => {
                        const mode = getFieldValue('window_mode');
                        return (
                          <Form.Item
                            label={mode === 'ratio' ? '预警阈值（检测占比）' : '预警阈值（检测次数）'}
                            name="window_threshold"
                            rules={[{ required: true }]}
                            extra={
                              mode === 'ratio'
                                ? '0-1之间，如0.3表示30%的帧检测到才告警'
                                : '正整数，如5表示检测到5次就告警'
                            }
                          >
                            {mode === 'ratio' ? (
                              <InputNumber min={0} max={1} step={0.1} style={{ width: '100%' }} />
                            ) : (
                              <InputNumber min={1} max={100} step={1} style={{ width: '100%' }} />
                            )}
                          </Form.Item>
                        );
                      }}
                    </Form.Item>

                    <Alert
                      message="模式说明"
                      description={
                        <ul className="window-mode-description">
                          <li>
                            <strong>占比模式</strong>：检测帧数/总帧数 ≥ 阈值，适合间歇性检测
                          </li>
                          <li>
                            <strong>次数模式</strong>：检测帧数 ≥ 阈值，适合快速响应
                          </li>
                          <li>
                            <strong>连续模式</strong>：最大连续检测次数 ≥ 阈值，适合持续性检测
                          </li>
                        </ul>
                      }
                      type="info"
                      showIcon
                    />
                  </div>
                ) : null
              }
            </Form.Item>
          </div>
        </div>

        {/* 扩展配置 */}
        <div className="form-section">
          <div className="form-section-header">
            <SettingOutlined className="section-icon" />
            <span className="section-title">扩展配置</span>
          </div>

          <div className="form-section-content">
            <Form.Item
              label="扩展配置 (JSON)"
              name="ext_config_json"
              extra="可选的扩展参数，JSON格式"
            >
              <TextArea
                rows={4}
                placeholder='{"custom_param": "value"}'
                className="code-textarea"
              />
            </Form.Item>
          </div>
        </div>
      </Form>
    </Modal>
  );
};

export default AlgorithmForm;
