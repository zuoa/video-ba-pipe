import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from '@umijs/max';
import {
  Steps,
  Button,
  Form,
  Input,
  InputNumber,
  Select,
  Switch,
  Card,
  Space,
  Alert,
  message,
  Spin,
  Divider,
  Row,
  Col,
} from 'antd';
import {
  CheckOutlined,
  ArrowLeftOutlined,
  ArrowRightOutlined,
  ApiOutlined,
  CodeOutlined,
  UploadOutlined,
  InfoCircleOutlined,
  ClockCircleOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  DeleteOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { getDetectorTemplates, getScripts, getModels, createAlgorithm } from '@/services/api';
import type { DetectorTemplate, Script } from '@/services/api';
import './index.css';

const { TextArea } = Input;
const { Option } = Select;

interface ConfigSchema {
  [key: string]: {
    type: string;
    label?: string;
    description?: string;
    required?: boolean;
    default?: any;
    options?: any[];
    min?: number;
    max?: number;
    step?: number;
    placeholder?: string;
    multiple?: boolean;
    item_schema?: any;
    filters?: {
      model_type?: string[];
      framework?: string[];
    };
  };
}

interface SelectedDetector {
  type: 'template' | 'script';
  id: number | null;
  name: string;
  description: string;
  scriptPath: string;
  configSchema?: ConfigSchema;
}

export default function AlgorithmWizard() {
  const navigate = useNavigate();
  const [currentStep, setCurrentStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [templates, setTemplates] = useState<DetectorTemplate[]>([]);
  const [scripts, setScripts] = useState<Script[]>([]);
  const [models, setModels] = useState<any[]>([]);
  const [selectedDetector, setSelectedDetector] = useState<SelectedDetector | null>(null);
  const [configSchema, setConfigSchema] = useState<ConfigSchema>({});
  const [modelItems, setModelItems] = useState<{ [key: string]: string[] }>({});
  const [form] = Form.useForm();

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [templatesData, scriptsData, modelsData] = await Promise.all([
        getDetectorTemplates(),
        getScripts(),
        getModels(),
      ]);
      setTemplates(templatesData?.templates || []);
      setScripts(scriptsData?.scripts || []);
      setModels(modelsData?.models || []);
    } catch (error) {
      message.error('加载数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSelectDetector = async (detector: SelectedDetector) => {
    setSelectedDetector(detector);

    if (detector.scriptPath) {
      try {
        const response = await fetch(`/api/detector-templates/script-config/${encodeURIComponent(detector.scriptPath)}`);
        const data = await response.json();
        if (data.success) {
          setConfigSchema(data.config_schema || {});
        }
      } catch (error) {
        console.error('加载配置模式失败:', error);
        setConfigSchema({});
      }
    }
  };

  const handleNext = async () => {
    if (currentStep === 0) {
      if (!selectedDetector) {
        message.warning('请先选择一个检测器');
        return;
      }
      setCurrentStep(1);
    } else if (currentStep === 1) {
      try {
        await form.validateFields();
        setCurrentStep(2);
      } catch (error) {
        message.warning('请完善配置信息');
      }
    }
  };

  const handlePrev = () => {
    setCurrentStep(currentStep - 1);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();

      const scriptConfig = collectConfigData();

      const data = {
        name: values.algorithmName,
        script_path: selectedDetector!.scriptPath,
        plugin_module: 'script_algorithm',
        script_type: 'script',
        script_config: JSON.stringify(scriptConfig),
        detector_template_id: selectedDetector!.type === 'template' ? selectedDetector!.id : null,
        interval_seconds: values.intervalSeconds,
        runtime_timeout: values.runtimeTimeout,
        memory_limit_mb: values.memoryLimitMb,
        enable_window_check: values.enableWindowCheck,
        window_size: values.windowSize,
        window_mode: values.windowMode,
        window_threshold: values.windowThreshold,
        label_name: values.labelName,
        model_json: JSON.stringify({ models: [] }),
        model_ids: JSON.stringify([]),
        ext_config_json: JSON.stringify({}),
      };

      await createAlgorithm(data);
      message.success('算法创建成功！');
      navigate('/algorithms');
    } catch (error) {
      message.error('创建失败');
    }
  };

  const collectConfigData = () => {
    const config: any = {};

    for (const [key, field] of Object.entries(configSchema)) {
      if (field.type === 'model_list') {
        const items = [];
        const itemIds = modelItems[key] || [];
        const itemSchema = field.item_schema || {};

        for (const itemId of itemIds) {
          const item: any = {};
          for (const [subKey, subField] of Object.entries(itemSchema)) {
            const fieldId = `model_${key}_${itemId}_${subKey}`;
            const value = form.getFieldValue(fieldId);

            if (subField.type === 'number' || subField.type === 'float' || subField.type === 'int') {
              item[subKey] = value !== undefined ? parseFloat(value) : subField.default;
            } else if (subField.type === 'model_select') {
              item[subKey] = value ? parseInt(value) : null;
            } else if (subField.type === 'boolean') {
              item[subKey] = value || false;
            } else {
              item[subKey] = value || subField.default || '';
            }
          }
          items.push(item);
        }
        config[key] = items;
      } else {
        const value = form.getFieldValue(`config_${key}`);
        if (field.type === 'number' || field.type === 'float' || field.type === 'int') {
          config[key] = value !== undefined ? parseFloat(value) : field.default;
        } else if (field.type === 'model_select') {
          config[key] = value ? parseInt(value) : null;
        } else if (field.type === 'boolean') {
          config[key] = value || false;
        } else if (field.type === 'int_list') {
          try {
            config[key] = value ? JSON.parse(value) : [];
          } catch (e) {
            config[key] = [];
          }
        } else {
          config[key] = value || field.default || '';
        }
      }
    }

    return config;
  };

  const handleCancel = () => {
    navigate('/algorithms');
  };

  const addModelItem = (fieldKey: string) => {
    const itemId = `model_item_${fieldKey}_${Date.now()}`;
    setModelItems(prev => ({
      ...prev,
      [fieldKey]: [...(prev[fieldKey] || []), itemId],
    }));
  };

  const removeModelItem = (fieldKey: string, itemId: string) => {
    setModelItems(prev => ({
      ...prev,
      [fieldKey]: (prev[fieldKey] || []).filter(id => id !== itemId),
    }));
  };

  const renderStep1 = () => {
    return (
      <div className="wizard-step-content">
        <div className="detector-section">
          <h3 className="section-title">
            <ApiOutlined className="title-icon" />
            系统模板（推荐）
          </h3>
          <Row gutter={[12, 12]}>
            {templates.filter(t => t.is_system).map(template => (
              <Col key={template.id} xs={24} sm={12} lg={8} xl={6}>
                <Card
                  hoverable
                  className={`detector-card ${selectedDetector?.id === template.id ? 'selected' : ''}`}
                  onClick={() => handleSelectDetector({
                    type: 'template',
                    id: template.id,
                    name: template.name,
                    description: template.description || '',
                    scriptPath: template.script_path,
                  })}
                >
                  <ApiOutlined className="card-icon" />
                  <div className="detector-card-content">
                    <h4 className="card-title">{template.name}</h4>
                    <p className="card-description">{template.description}</p>
                    {template.tags_list && template.tags_list.length > 0 && (
                      <div className="card-tags">
                        {template.tags_list.map(tag => (
                          <span key={tag} className="tag">{tag}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </Card>
              </Col>
            ))}
          </Row>
        </div>

        <Divider />

        <div className="detector-section">
          <h3 className="section-title">
            <CodeOutlined className="title-icon" />
            我的脚本
          </h3>
          <Row gutter={[12, 12]}>
            {scripts.length === 0 ? (
              <Col span={24}>
                <Alert
                  message="暂无自定义脚本"
                  description="请先在脚本管理页面上传脚本"
                  type="info"
                  showIcon
                />
              </Col>
            ) : (
              scripts.map(script => (
                <Col key={script.path} xs={24} sm={12} lg={8} xl={6}>
                  <Card
                    hoverable
                    className={`detector-card ${selectedDetector?.scriptPath === script.path ? 'selected' : ''}`}
                    onClick={() => handleSelectDetector({
                      type: 'script',
                      id: null,
                      name: script.name,
                      description: script.path,
                      scriptPath: script.path,
                    })}
                  >
                    <CodeOutlined className="card-icon script-icon" />
                    <div className="detector-card-content">
                      <h4 className="card-title">{script.name}</h4>
                      <p className="card-description">{script.path}</p>
                    </div>
                  </Card>
                </Col>
              ))
            )}
          </Row>
        </div>

        <div className="upload-script-section">
          <Button
            icon={<UploadOutlined />}
            onClick={() => window.open('/scripts', '_blank')}
            className="upload-script-btn"
          >
            上传新脚本
          </Button>
        </div>
      </div>
    );
  };

  const renderStep2 = () => {
    return (
      <div className="wizard-step-content">
        <Alert
          message={
            <Space>
              <InfoCircleOutlined />
              <span>
                <strong>{selectedDetector?.name}</strong>
                <span className="detector-description"> - {selectedDetector?.description}</span>
              </span>
            </Space>
          }
          type="info"
          showIcon
          className="selected-detector-info"
        />

        <div className="config-form">
          {Object.keys(configSchema).length === 0 ? (
            <Alert
              message="此检测器无需额外配置"
              type="success"
              showIcon
            />
          ) : (
            Object.entries(configSchema).map(([key, field]) => (
              <Form.Item
                key={key}
                label={
                  <Space>
                    {field.label || key}
                    {field.required && <span className="required">*</span>}
                  </Space>
                }
                extra={field.description}
              >
                {renderConfigField(key, field)}
              </Form.Item>
            ))
          )}
        </div>
      </div>
    );
  };

  const renderConfigField = (key: string, field: any) => {
    const defaultValue = field.default !== undefined ? field.default : '';

    switch (field.type) {
      case 'model_list':
        return (
          <div className="model-list-container">
            <div className="model-items">
              {(modelItems[key] || []).map(itemId => (
                <Card key={itemId} size="small" className="model-item-card">
                  <div className="model-item-header">
                    <span>模型配置</span>
                    <Button
                      type="text"
                      danger
                      size="small"
                      icon={<DeleteOutlined />}
                      onClick={() => removeModelItem(key, itemId)}
                    >
                      删除
                    </Button>
                  </div>
                  {renderModelItemFields(key, field.item_schema || {}, itemId)}
                </Card>
              ))}
            </div>
            <Button
              type="dashed"
              icon={<PlusOutlined />}
              onClick={() => addModelItem(key)}
              block
            >
              添加模型
            </Button>
          </div>
        );

      case 'model_select':
        return (
          <Select
            id={`config_${key}`}
            placeholder="选择模型..."
            allowClear
          >
            {models.filter(m => {
              if (field.filters) {
                if (field.filters.model_type && !field.filters.model_type.includes(m.model_type)) {
                  return false;
                }
                if (field.filters.framework && !field.filters.framework.includes(m.framework)) {
                  return false;
                }
              }
              return m.enabled;
            }).map(m => (
              <Option key={m.id} value={m.id}>{m.name} ({m.model_type})</Option>
            ))}
          </Select>
        );

      case 'float':
      case 'int':
        return (
          <InputNumber
            id={`config_${key}`}
            defaultValue={defaultValue}
            min={field.min}
            max={field.max}
            step={field.step || (field.type === 'int' ? 1 : 0.01)}
            style={{ width: '100%' }}
          />
        );

      case 'boolean':
        return (
          <Switch id={`config_${key}`} defaultChecked={defaultValue} />
        );

      case 'select':
        return (
          <Select id={`config_${key}`} defaultValue={defaultValue}>
            {field.options?.map((opt: any) => {
              const value = typeof opt === 'object' ? opt.value : opt;
              const label = typeof opt === 'object' ? opt.label : opt;
              return <Option key={value} value={value}>{label}</Option>;
            })}
          </Select>
        );

      case 'color':
        return (
          <Input
            type="color"
            id={`config_${key}`}
            defaultValue={defaultValue || '#FF0000'}
            style={{ width: 100 }}
          />
        );

      case 'int_list':
        return (
          <Input
            id={`config_${key}`}
            defaultValue={defaultValue ? JSON.stringify(defaultValue) : '[]'}
            placeholder={field.placeholder || '例如: [0, 1, 2]'}
          />
        );

      default:
        return (
          <Input
            id={`config_${key}`}
            defaultValue={defaultValue}
            placeholder={field.placeholder}
          />
        );
    }
  };

  const renderModelItemFields = (fieldKey: string, itemSchema: any, itemId: string) => {
    return Object.entries(itemSchema).map(([subKey, subField]: [string, any]) => {
      const fieldId = `model_${fieldKey}_${itemId}_${subKey}`;
      const defaultValue = subField.default !== undefined ? subField.default : '';

      return (
        <Form.Item
          key={fieldId}
          label={subField.label || subKey}
          style={{ marginBottom: 8 }}
        >
          {subField.type === 'model_select' ? (
            <Select
              id={fieldId}
              defaultValue={defaultValue}
              placeholder="选择模型..."
            >
              {models.filter(m => m.enabled).map(m => (
                <Option key={m.id} value={m.id}>{m.name} ({m.model_type})</Option>
              ))}
            </Select>
          ) : subField.type === 'float' || subField.type === 'int' ? (
            <InputNumber
              id={fieldId}
              defaultValue={defaultValue}
              min={subField.min}
              max={subField.max}
              step={subField.step || 0.01}
              style={{ width: '100%' }}
            />
          ) : subField.type === 'color' ? (
            <Input
              type="color"
              id={fieldId}
              defaultValue={defaultValue || '#FF0000'}
              style={{ width: 100 }}
            />
          ) : (
            <Input
              id={fieldId}
              defaultValue={defaultValue}
              placeholder={subField.placeholder}
            />
          )}
        </Form.Item>
      );
    });
  };

  const renderStep3 = () => {
    return (
      <div className="wizard-step-content">
        <Form form={form} layout="vertical">
          <Card title={<Space><InfoCircleOutlined />基础信息</Space>} className="config-card">
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  label="算法名称"
                  name="algorithmName"
                  rules={[{ required: true, message: '请输入算法名称' }]}
                >
                  <Input placeholder="例如: 门口人员检测" />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  label="检测间隔（秒）"
                  name="intervalSeconds"
                  initialValue={1}
                  rules={[{ required: true, message: '请输入检测间隔' }]}
                >
                  <InputNumber min={0.1} max={60} step={0.1} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>
          </Card>

          <Card title={<Space><ThunderboltOutlined />性能配置</Space>} className="config-card">
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  label="运行超时（秒）"
                  name="runtimeTimeout"
                  initialValue={30}
                >
                  <InputNumber min={1} max={300} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  label="内存限制（MB）"
                  name="memoryLimitMb"
                  initialValue={512}
                >
                  <InputNumber min={64} max={4096} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>
          </Card>

          <Card title={<Space><ClockCircleOutlined />时间窗口检测（误报抑制）</Space>} className="config-card">
            <Form.Item
              label="启用时间窗口检测"
              name="enableWindowCheck"
              valuePropName="checked"
              initialValue={false}
              extra="在时间窗口内多次检测确认后才触发告警，减少误报"
            >
              <Switch />
            </Form.Item>

            <Form.Item noStyle shouldUpdate={(prev, curr) => prev.enableWindowCheck !== curr.enableWindowCheck}>
              {({ getFieldValue }) =>
                getFieldValue('enableWindowCheck') ? (
                  <Row gutter={16}>
                    <Col span={8}>
                      <Form.Item
                        label="窗口大小（秒）"
                        name="windowSize"
                        initialValue={30}
                        rules={[{ required: true }]}
                      >
                        <InputNumber min={5} max={300} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item
                        label="预警模式"
                        name="windowMode"
                        initialValue="ratio"
                        rules={[{ required: true }]}
                      >
                        <Select>
                          <Option value="ratio">占比模式</Option>
                          <Option value="count">次数模式</Option>
                          <Option value="consecutive">连续模式</Option>
                        </Select>
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item noStyle shouldUpdate={(prev, curr) => prev.windowMode !== curr.windowMode}>
                        {({ getFieldValue }) => {
                          const mode = getFieldValue('windowMode');
                          return (
                            <Form.Item
                              label={mode === 'ratio' ? '预警阈值（检测占比）' : '预警阈值（检测次数）'}
                              name="windowThreshold"
                              initialValue={mode === 'ratio' ? 0.3 : 5}
                              rules={[{ required: true }]}
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
                    </Col>
                  </Row>
                ) : null
              }
            </Form.Item>

            <Alert
              message="模式说明"
              description={
                <ul className="window-mode-description">
                  <li><strong>占比模式</strong>：检测帧数/总帧数 ≥ 阈值，适合间歇性检测</li>
                  <li><strong>次数模式</strong>：检测帧数 ≥ 阈值，适合快速响应</li>
                  <li><strong>连续模式</strong>：最大连续检测次数 ≥ 阈值，适合持续性检测</li>
                </ul>
              }
              type="info"
            />
          </Card>

          <Card title={<Space><SettingOutlined />显示标签</Space>} className="config-card">
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  label="标签名称"
                  name="labelName"
                  initialValue="Object"
                >
                  <Input />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  label="标签颜色"
                  name="labelColor"
                  initialValue="#FF0000"
                >
                  <Input type="color" style={{ width: 100 }} />
                </Form.Item>
              </Col>
            </Row>
          </Card>
        </Form>
      </div>
    );
  };

  const steps = [
    {
      title: '选择检测器',
      icon: <ApiOutlined />,
      description: '选择系统模板或自定义脚本',
    },
    {
      title: '配置参数',
      icon: <SettingOutlined />,
      description: '配置检测器参数',
    },
    {
      title: '执行配置',
      icon: <ThunderboltOutlined />,
      description: '配置执行和告警参数',
    },
  ];

  return (
    <div className="algorithm-wizard-page">
      <Spin spinning={loading}>
        <div className="wizard-container">
          <div className="wizard-header">
            <h1>算法配置向导</h1>
            <Button onClick={handleCancel}>返回</Button>
          </div>

          <Steps
            current={currentStep}
            items={steps.map((step, index) => ({
              title: step.title,
              description: step.description,
              icon: index < currentStep ? <CheckOutlined /> : step.icon,
            }))}
            className="wizard-steps"
          />

          <div className="wizard-content">
            {currentStep === 0 && renderStep1()}
            {currentStep === 1 && renderStep2()}
            {currentStep === 2 && renderStep3()}
          </div>

          <div className="wizard-footer">
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={handlePrev}
              disabled={currentStep === 0}
            >
              上一步
            </Button>
            <Space>
              {currentStep < 2 ? (
                <Button
                  type="primary"
                  icon={<ArrowRightOutlined />}
                  onClick={handleNext}
                >
                  下一步
                </Button>
              ) : (
                <Button
                  type="primary"
                  icon={<CheckOutlined />}
                  onClick={handleSubmit}
                >
                  创建算法
                </Button>
              )}
            </Space>
          </div>
        </div>
      </Spin>
    </div>
  );
}

