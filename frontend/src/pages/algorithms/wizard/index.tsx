import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate, useSearchParams } from '@umijs/max';
import {
  Steps,
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
import Button from '@/components/common/AppButton';
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
  RobotOutlined,
  FileSearchOutlined,
  ApartmentOutlined,
  ControlOutlined,
} from '@ant-design/icons';
import {
  getScripts,
  getModels,
  createAlgorithm,
  getAlgorithms,
  updateAlgorithm,
  getScriptConfigSchema,
  getPluginModules,
} from '@/services/api';
import type { Script } from '@/services/api';
import CascadeEditor, {
  createEmptyCascadeConfig,
  getCascadeOutput,
  normalizeCascadeForEditor,
  validateCascadeGraph,
  type CascadeConfig,
} from './CascadeEditor';
import './index.css';

const { TextArea } = Input;
const { Option } = Select;

type AlgorithmType = 'script' | 'vl' | 'ocr' | 'cascade';

interface DetectorPreset extends Script {
  description: string;
}

const DETECTOR_PRESETS: readonly DetectorPreset[] = [
  {
    name: '通用单模型',
    path: 'templates/adaptive_yolo_detector.py',
    description: '单模型检测，自动适配 Ultralytics、ONNX 和 RKNN 后端',
  },
  {
    name: '并行多模型共同确认',
    path: 'templates/yolo_detector.py',
    description: '多个模型对同一画面并行推理，通过 IOU 匹配共同确认目标',
  },
];

const getAvailableDetectorPresets = (scripts: Script[]): DetectorPreset[] => {
  const availablePaths = new Set(scripts.map(script => script.path));
  return DETECTOR_PRESETS.filter(preset => availablePaths.has(preset.path));
};

const DEFAULT_VL_PROMPT = `请判断画面中是否存在需要关注的目标或事件。
如果命中，请为每个目标或事件返回一个检测项；无法可靠定位时 bbox 返回 null。
请在 reason 中简要说明判断依据。`;

const validateJsonObject = (_: unknown, value: string) => {
  if (!value) return Promise.resolve();
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? Promise.resolve()
      : Promise.reject(new Error('请输入 JSON 对象'));
  } catch {
    return Promise.reject(new Error('JSON 格式不正确'));
  }
};

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

type ConfigField = ConfigSchema[string];

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
  const [searchParams] = useSearchParams();
  const editId = searchParams.get('edit');
  const [currentStep, setCurrentStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [scripts, setScripts] = useState<DetectorPreset[]>([]);
  const [models, setModels] = useState<any[]>([]);
  const [selectedDetector, setSelectedDetector] = useState<SelectedDetector | null>(null);
  const [configSchema, setConfigSchema] = useState<ConfigSchema>({});
  const [modelItems, setModelItems] = useState<{ [key: string]: string[] }>({});
  const [editingAlgorithm, setEditingAlgorithm] = useState<any>(null);
  const [algorithmType, setAlgorithmType] = useState<AlgorithmType>('script');
  const [cascadeConfig, setCascadeConfig] = useState<CascadeConfig>(createEmptyCascadeConfig);
  const [ocrRuntimeAvailable, setOcrRuntimeAvailable] = useState(false);
  const [ocrRuntimeError, setOcrRuntimeError] = useState('');
  const [form] = Form.useForm();

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [scriptsData, modelsData, algorithmsData, pluginData] = await Promise.all([
        getScripts().catch(() => ({ scripts: [] })),
        getModels().catch(() => ({ models: [] })),
        editId ? getAlgorithms() : Promise.resolve([]),
        getPluginModules().catch(() => ({ modules: [], capabilities: {} })),
      ]);
      setScripts(getAvailableDetectorPresets(scriptsData?.scripts || []));
      setModels(modelsData?.models || []);
      setOcrRuntimeAvailable(pluginData?.capabilities?.ocr?.available === true);
      setOcrRuntimeError(pluginData?.capabilities?.ocr?.error || '');

      if (editId) {
        const algorithm = algorithmsData.find((a: any) => a.id === parseInt(editId));
        if (algorithm) {
          setEditingAlgorithm(algorithm);
          await loadEditData(algorithm);
        } else {
          message.error('算法不存在');
          navigate('/algorithms');
        }
      }
    } catch (error) {
      message.error('加载数据失败');
    } finally {
      setLoading(false);
    }
  }, [editId, navigate]);

  const loadEditData = async (algorithm: any) => {
    const currentType: AlgorithmType = ['vl', 'ocr', 'cascade'].includes(algorithm.algorithm_type)
      ? algorithm.algorithm_type
      : 'script';
    setAlgorithmType(currentType);

    if (currentType === 'vl') {
      const vlConfig = algorithm.vl_config || {};
      setSelectedDetector({
        type: 'template',
        id: null,
        name: '视觉语言模型',
        description: 'OpenAI 兼容 VL API',
        scriptPath: '',
      });
      setConfigSchema({});
      form.setFieldsValue({
        algorithmName: algorithm.name,
        algorithmDescription: algorithm.description || '',
        intervalSeconds: algorithm.interval_seconds || 1,
        runtimeTimeout: algorithm.runtime_timeout || vlConfig.timeout_seconds || 30,
        enableWindowCheck: algorithm.enable_window_check || false,
        windowSize: algorithm.window_size || 30,
        windowMode: algorithm.window_mode || 'ratio',
        windowThreshold: algorithm.window_threshold || 0.3,
        labelName: algorithm.label_name || 'VL Result',
        labelColor: algorithm.label_color || '#13c2c2',
        vlBaseUrl: vlConfig.base_url,
        vlApiKey: '',
        vlModelName: vlConfig.model_name,
        vlPromptTemplate: vlConfig.prompt_template || DEFAULT_VL_PROMPT,
        vlTemperature: vlConfig.temperature ?? 0,
        vlMaxTokens: vlConfig.max_tokens || 512,
        vlTimeoutSeconds: vlConfig.timeout_seconds || 30,
        vlImageDetail: vlConfig.image_detail || 'auto',
        vlExtraHeaders: JSON.stringify(vlConfig.extra_headers || {}, null, 2),
        vlExtraBody: JSON.stringify(vlConfig.extra_body || {}, null, 2),
      });
      return;
    }

    if (currentType === 'ocr') {
      const ocrConfig = algorithm.ocr_config || {};
      setSelectedDetector({
        type: 'template',
        id: null,
        name: 'PaddleOCR',
        description: '本地文字检测与识别',
        scriptPath: '',
      });
      setConfigSchema({});
      form.setFieldsValue({
        algorithmName: algorithm.name,
        algorithmDescription: algorithm.description || '',
        intervalSeconds: algorithm.interval_seconds || 1,
        labelName: algorithm.label_name || 'OCR Text',
        labelColor: algorithm.label_color || '#1677ff',
        ocrDetectionModelId: ocrConfig.detection_model_id,
        ocrRecognitionModelId: ocrConfig.recognition_model_id,
        ocrDevice: ocrConfig.device || 'auto',
        ocrRecognitionScoreThreshold: ocrConfig.recognition_score_threshold ?? 0.5,
        ocrDetectionThreshold: ocrConfig.detection_threshold,
        ocrBoxThreshold: ocrConfig.box_threshold,
        ocrUnclipRatio: ocrConfig.unclip_ratio,
        ocrLimitSideLen: ocrConfig.limit_side_len,
        ocrRecognitionBatchSize: ocrConfig.recognition_batch_size || 1,
      });
      return;
    }

    if (currentType === 'cascade') {
      const loadedCascade = algorithm.cascade_config || createEmptyCascadeConfig();
      setCascadeConfig(normalizeCascadeForEditor(loadedCascade));
      setSelectedDetector({
        type: 'template',
        id: null,
        name: '组合检测',
        description: '用检测数据流和判定规则形成一个业务结果',
        scriptPath: '',
      });
      setConfigSchema({});
      form.setFieldsValue({
        algorithmName: algorithm.name,
        algorithmDescription: algorithm.description || '',
        intervalSeconds: algorithm.interval_seconds || 1,
        enableWindowCheck: algorithm.enable_window_check || false,
        windowSize: algorithm.window_size || 30,
        windowMode: algorithm.window_mode || 'ratio',
        windowThreshold: algorithm.window_threshold || 0.3,
      });
      return;
    }

    const detector = {
      type: 'script' as const,
      id: null,
      name: algorithm.name,
      description: algorithm.description || '',
      scriptPath: algorithm.script_path,
    };
    
    setSelectedDetector(detector);

    if (detector.scriptPath) {
      try {
        const data = await getScriptConfigSchema(detector.scriptPath);
        if (data.success) {
          const loadedSchema = data.config_schema || {};
          setConfigSchema(loadedSchema);
          
          form.setFieldsValue({
            algorithmName: algorithm.name,
            algorithmDescription: algorithm.description || '',
            intervalSeconds: algorithm.interval_seconds || 1,
            runtimeTimeout: algorithm.runtime_timeout || 30,
            memoryLimitMb: algorithm.memory_limit_mb || 512,
            enableWindowCheck: algorithm.enable_window_check || false,
            windowSize: algorithm.window_size || 30,
            windowMode: algorithm.window_mode || 'ratio',
            windowThreshold: algorithm.window_threshold || 0.3,
            labelName: algorithm.label_name || 'Object',
            labelColor: algorithm.label_color || '#FF0000',
          });

          try {
            const scriptConfig = JSON.parse(algorithm.script_config || '{}');
            for (const [key, value] of Object.entries(scriptConfig)) {
              const fieldSchema = loadedSchema[key];
              if (fieldSchema?.type === 'model_list' && Array.isArray(value)) {
                const itemIds: string[] = [];
                value.forEach((item: any, index: number) => {
                  const itemId = `model_item_${key}_${Date.now()}_${index}`;
                  itemIds.push(itemId);
                  for (const [subKey, subValue] of Object.entries(item)) {
                    form.setFieldValue(`model_${key}_${itemId}_${subKey}`, subValue);
                  }
                });
                setModelItems(prev => ({ ...prev, [key]: itemIds }));
              } else if (fieldSchema?.type === 'int_list' && Array.isArray(value)) {
                form.setFieldValue(`config_${key}`, JSON.stringify(value));
              } else {
                form.setFieldValue(`config_${key}`, value);
              }
            }
          } catch (error) {
            console.error('解析脚本配置失败:', error);
          }
        }
      } catch (error) {
        console.error('加载配置模式失败:', error);
        setConfigSchema({});
      }
    }
  };

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSelectDetector = async (detector: SelectedDetector) => {
    setAlgorithmType('script');
    setSelectedDetector(detector);

    if (detector.scriptPath) {
      try {
        const data = await getScriptConfigSchema(detector.scriptPath);
        if (data.success) {
          setConfigSchema(data.config_schema || {});
        }
      } catch (error) {
        console.error('加载配置模式失败:', error);
        setConfigSchema({});
      }
    }
  };

  const handleSelectVl = () => {
    setAlgorithmType('vl');
    setSelectedDetector({
      type: 'template',
      id: null,
      name: '视觉语言模型',
      description: 'OpenAI 兼容 VL API',
      scriptPath: '',
    });
    setConfigSchema({});
    form.setFieldsValue({
      vlPromptTemplate: form.getFieldValue('vlPromptTemplate') || DEFAULT_VL_PROMPT,
      vlTemperature: form.getFieldValue('vlTemperature') ?? 0,
      vlMaxTokens: form.getFieldValue('vlMaxTokens') || 512,
      vlTimeoutSeconds: form.getFieldValue('vlTimeoutSeconds') || 30,
      vlImageDetail: form.getFieldValue('vlImageDetail') || 'auto',
      vlExtraHeaders: form.getFieldValue('vlExtraHeaders') || '{}',
      vlExtraBody: form.getFieldValue('vlExtraBody') || '{}',
      labelName: form.getFieldValue('labelName') || 'VL Result',
      labelColor: form.getFieldValue('labelColor') || '#13c2c2',
    });
  };

  const handleSelectOcr = () => {
    if (!ocrRuntimeAvailable) {
      message.error(ocrRuntimeError || '当前运行环境不支持 OCR');
      return;
    }
    setAlgorithmType('ocr');
    setSelectedDetector({
      type: 'template',
      id: null,
      name: 'PaddleOCR',
      description: '本地文字检测与识别',
      scriptPath: '',
    });
    setConfigSchema({});
    form.setFieldsValue({
      ocrDevice: form.getFieldValue('ocrDevice') || 'auto',
      ocrRecognitionScoreThreshold: form.getFieldValue('ocrRecognitionScoreThreshold') ?? 0.5,
      ocrRecognitionBatchSize: form.getFieldValue('ocrRecognitionBatchSize') || 1,
      labelName: form.getFieldValue('labelName') || 'OCR Text',
      labelColor: form.getFieldValue('labelColor') || '#1677ff',
    });
  };

  const handleSelectCascade = () => {
    const isNewSelection = algorithmType !== 'cascade';
    setAlgorithmType('cascade');
    if (isNewSelection) setCascadeConfig(createEmptyCascadeConfig());
    setSelectedDetector({
      type: 'template',
      id: null,
      name: '组合检测',
      description: '用画布组合多个检测模型和判定规则',
      scriptPath: '',
    });
    setConfigSchema({});
    if (isNewSelection) {
      form.setFieldsValue({
        enableWindowCheck: true,
        windowSize: 30,
        windowMode: 'ratio',
        windowThreshold: 0.3,
      });
    }
  };

  const validateCascade = (): string | null => {
    return validateCascadeGraph(cascadeConfig);
  };

  const handleNext = async () => {
    if (currentStep === 0) {
      if (!selectedDetector) {
        message.warning('请先选择一个检测器');
        return;
      }
      setCurrentStep(1);
    } else if (currentStep === 1) {
      if (algorithmType === 'cascade') {
        const cascadeError = validateCascade();
        if (cascadeError) {
          message.warning(cascadeError);
          return;
        }
      }
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

  const collectModelIdsFromConfig = (config: any) => {
    // 从 script_config 中提取模型 ID 列表
    const modelIds: number[] = [];

    if (config.models && Array.isArray(config.models)) {
      for (const modelItem of config.models) {
        if (modelItem.model_id && typeof modelItem.model_id === 'number') {
          modelIds.push(modelItem.model_id);
        }
      }
    }

    return modelIds;
  };

  const validateModelSelection = (config: any): { valid: boolean; error?: string } => {
    // 验证模型选择是否完整
    if (config.models && Array.isArray(config.models)) {
      for (let i = 0; i < config.models.length; i++) {
        const modelItem = config.models[i];
        if (!modelItem.model_id || typeof modelItem.model_id !== 'number') {
          return {
            valid: false,
            error: `请为第 ${i + 1} 个模型选择有效的模型`
          };
        }
      }
      if (config.models.length === 0) {
        return {
          valid: false,
          error: '请至少添加一个模型'
        };
      }
    }
    return { valid: true };
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();

      if (algorithmType === 'vl') {
        const data = {
          name: values.algorithmName,
          description: values.algorithmDescription,
          algorithm_type: 'vl',
          script_path: '',
          script_config: '{}',
          plugin_module: 'vl_algorithm',
          interval_seconds: values.intervalSeconds,
          enable_window_check: values.enableWindowCheck,
          window_size: values.windowSize,
          window_mode: values.windowMode,
          window_threshold: values.windowThreshold,
          label_name: values.labelName,
          label_color: values.labelColor,
          vl_config: {
            base_url: values.vlBaseUrl,
            api_key: values.vlApiKey || '',
            model_name: values.vlModelName,
            prompt_template: values.vlPromptTemplate,
            temperature: values.vlTemperature,
            max_tokens: values.vlMaxTokens,
            timeout_seconds: values.vlTimeoutSeconds,
            image_detail: values.vlImageDetail,
            extra_headers: JSON.parse(values.vlExtraHeaders || '{}'),
            extra_body: JSON.parse(values.vlExtraBody || '{}'),
          },
        };

        if (editingAlgorithm) {
          await updateAlgorithm(editingAlgorithm.id, data);
          message.success('VL 算法更新成功！');
        } else {
          await createAlgorithm(data);
          message.success('VL 算法创建成功！');
        }
        navigate('/algorithms');
        return;
      }

      if (algorithmType === 'ocr') {
        if (!ocrRuntimeAvailable) {
          message.error(ocrRuntimeError || '当前运行环境不支持 OCR，无法保存');
          return;
        }
        const data = {
          name: values.algorithmName,
          description: values.algorithmDescription,
          algorithm_type: 'ocr',
          script_path: '',
          script_config: '{}',
          plugin_module: 'ocr_algorithm',
          interval_seconds: values.intervalSeconds,
          label_name: values.labelName,
          label_color: values.labelColor,
          ocr_config: {
            detection_model_id: values.ocrDetectionModelId,
            recognition_model_id: values.ocrRecognitionModelId,
            device: values.ocrDevice || 'auto',
            recognition_score_threshold: values.ocrRecognitionScoreThreshold,
            detection_threshold: values.ocrDetectionThreshold ?? null,
            box_threshold: values.ocrBoxThreshold ?? null,
            unclip_ratio: values.ocrUnclipRatio ?? null,
            limit_side_len: values.ocrLimitSideLen ?? null,
            recognition_batch_size: values.ocrRecognitionBatchSize || 1,
          },
        };
        if (editingAlgorithm) {
          await updateAlgorithm(editingAlgorithm.id, data);
          message.success('OCR 算法更新成功！');
        } else {
          await createAlgorithm(data);
          message.success('OCR 算法创建成功！');
        }
        navigate('/algorithms');
        return;
      }

      if (algorithmType === 'cascade') {
        const cascadeError = validateCascade();
        if (cascadeError) {
          message.error(cascadeError);
          return;
        }
        const cascadeOutput = getCascadeOutput(cascadeConfig);
        const data = {
          name: values.algorithmName,
          description: values.algorithmDescription,
          algorithm_type: 'cascade',
          script_path: '',
          script_config: '{}',
          plugin_module: 'cascade_algorithm',
          interval_seconds: values.intervalSeconds,
          enable_window_check: values.enableWindowCheck,
          window_size: values.windowSize,
          window_mode: values.windowMode,
          window_threshold: values.windowThreshold,
          label_name: cascadeOutput?.label || '组合事件',
          label_color: cascadeOutput?.color || '#ff4d4f',
          cascade_config: cascadeConfig,
        };
        if (editingAlgorithm) {
          await updateAlgorithm(editingAlgorithm.id, data);
          message.success('组合检测算法更新成功！');
        } else {
          await createAlgorithm(data);
          message.success('组合检测算法创建成功！');
        }
        navigate('/algorithms');
        return;
      }

      const scriptConfig = collectConfigData();

      // 验证模型选择
      const validation = validateModelSelection(scriptConfig);
      if (!validation.valid) {
        message.error(validation.error);
        return;
      }

      // 从 script_config 中提取模型 ID
      const modelIds = collectModelIdsFromConfig(scriptConfig);

      const data = {
        name: values.algorithmName,
        description: values.algorithmDescription,
        algorithm_type: 'script',
        script_path: selectedDetector!.scriptPath,
        plugin_module: 'script_algorithm',
        script_type: 'script',
        script_config: JSON.stringify(scriptConfig),
        interval_seconds: values.intervalSeconds,
        runtime_timeout: values.runtimeTimeout,
        memory_limit_mb: values.memoryLimitMb,
        enable_window_check: values.enableWindowCheck,
        window_size: values.windowSize,
        window_mode: values.windowMode,
        window_threshold: values.windowThreshold,
        label_name: values.labelName,
        label_color: values.labelColor,
        model_json: JSON.stringify({ models: scriptConfig.models || [] }),
        model_ids: JSON.stringify(modelIds),
        ext_config_json: JSON.stringify({}),
      };

      if (editingAlgorithm) {
        await updateAlgorithm(editingAlgorithm.id, data);
        message.success('算法更新成功！');
      } else {
        await createAlgorithm(data);
        message.success('算法创建成功！');
      }
      navigate('/algorithms');
    } catch (error) {
      message.error(editingAlgorithm ? '更新失败' : '创建失败');
    }
  };

  const collectConfigData = () => {
    const config: any = {};

    for (const [key, field] of Object.entries(configSchema)) {
      if (field.type === 'model_list') {
        const items = [];
        const itemIds = modelItems[key] || [];
        const itemSchema = (field.item_schema || {}) as Record<string, ConfigField>;

        for (const itemId of itemIds) {
          const item: any = {};
          for (const [subKey, subField] of Object.entries(itemSchema)) {
            const fieldId = `model_${key}_${itemId}_${subKey}`;
            const value = form.getFieldValue(fieldId);

            if (subField.type === 'int') {
              item[subKey] = value !== undefined ? parseInt(value) : subField.default;
            } else if (subField.type === 'number' || subField.type === 'float') {
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
        if (field.type === 'int') {
          config[key] = value !== undefined ? parseInt(value) : (field.default !== undefined ? field.default : null);
        } else if (field.type === 'number' || field.type === 'float') {
          config[key] = value !== undefined ? parseFloat(value) : (field.default !== undefined ? field.default : null);
        } else if (field.type === 'model_select') {
          config[key] = value !== undefined && value !== null ? parseInt(value) : null;
        } else if (field.type === 'boolean') {
          config[key] = value !== undefined ? value : (field.default !== undefined ? field.default : false);
        } else if (field.type === 'int_list') {
          try {
            config[key] = value ? JSON.parse(value) : (field.default !== undefined ? field.default : []);
          } catch (e) {
            config[key] = field.default !== undefined ? field.default : [];
          }
        } else {
          config[key] = value !== undefined && value !== '' ? value : (field.default !== undefined ? field.default : '');
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
      <div className="detector-section">
        <h3 className="section-title">
          <ApiOutlined className="title-icon" />
          选择算法类型
        </h3>
        <Row gutter={[16, 16]} className="algorithm-type-grid">
          <Col xs={24} md={12} lg={6}>
            <Card
              hoverable
              className={`algorithm-type-card ${algorithmType === 'script' ? 'selected script' : ''}`}
              onClick={() => {
                setAlgorithmType('script');
                if (selectedDetector?.type !== 'script') setSelectedDetector(null);
              }}
            >
              <CodeOutlined className="algorithm-type-icon" />
              <div>
                <h4>脚本算法</h4>
                <p>执行本地检测脚本和模型，适合目标检测与规则计算。</p>
              </div>
            </Card>
          </Col>
          <Col xs={24} md={12} lg={6}>
            <Card
              hoverable
              className={`algorithm-type-card ${algorithmType === 'vl' ? 'selected vl' : ''}`}
              onClick={handleSelectVl}
            >
              <RobotOutlined className="algorithm-type-icon" />
              <div>
                <h4>VL 算法</h4>
                <p>调用 OpenAI 兼容视觉语言模型，输出可编排的语义检测结果。</p>
              </div>
            </Card>
          </Col>
          <Col xs={24} md={12} lg={6}>
            <Card
              hoverable
              className={`algorithm-type-card ${algorithmType === 'cascade' ? 'selected cascade' : ''}`}
              onClick={handleSelectCascade}
            >
              <ApartmentOutlined className="algorithm-type-icon" />
              <div>
                <h4>组合检测</h4>
                <p>在画布上组合检测步骤与 AND、OR、NOT 判定规则。</p>
              </div>
            </Card>
          </Col>
          {ocrRuntimeAvailable || editingAlgorithm?.algorithm_type === 'ocr' ? (
          <Col xs={24} md={12} lg={6}>
            <Card
              hoverable
              className={`algorithm-type-card ${algorithmType === 'ocr' ? 'selected ocr' : ''}`}
              onClick={handleSelectOcr}
            >
              <FileSearchOutlined className="algorithm-type-icon" />
              <div>
                <h4>OCR 算法</h4>
                <p>{ocrRuntimeAvailable
                  ? '使用本地 PaddleOCR 检测并识别视频画面中的文字。'
                  : '当前运行环境缺少 PaddleOCR，无法运行或保存。'}</p>
              </div>
            </Card>
          </Col>
          ) : null}
        </Row>

        {algorithmType === 'script' ? (
          <div className="algorithm-type-detail">
            <h3 className="section-title compact">
              <CodeOutlined className="title-icon" />
              选择检测脚本
            </h3>
            <Row gutter={[12, 12]}>
              {scripts.length === 0 ? (
                <Col span={24}>
                  <Alert
                    message="内置检测脚本不可用"
                    description="请检查通用单模型和多模型脚本是否已正确部署。"
                    type="warning"
                    showIcon
                  />
                </Col>
              ) : scripts.map(script => (
                <Col key={script.path} xs={24} md={12}>
                  <Card
                    hoverable
                    className={`detector-card ${selectedDetector?.scriptPath === script.path ? 'selected' : ''}`}
                    onClick={() => handleSelectDetector({
                      type: 'script',
                      id: null,
                      name: script.name,
                      description: script.description,
                      scriptPath: script.path,
                    })}
                  >
                    <CodeOutlined className="card-icon script-icon" />
                    <div className="detector-card-content">
                      <h4 className="card-title">{script.name}</h4>
                      <p className="card-description">{script.description}</p>
                      <code className="card-script-path">{script.path}</code>
                    </div>
                  </Card>
                </Col>
              ))}
            </Row>
            <div className="upload-script-section">
              <Button
                icon={<UploadOutlined />}
                onClick={() => window.open('/scripts', '_blank')}
                className="upload-script-btn"
              >
                管理脚本
              </Button>
            </div>
          </div>
        ) : algorithmType === 'vl' ? (
          <Alert
            className="vl-contract-callout"
            type="info"
            showIcon
            message="统一算法输出"
            description="VL 会把模型回答校验为 detections 和 metadata。语义结果可以不带检测框，但仍能参与条件计数和告警。"
          />
        ) : algorithmType === 'ocr' ? (
          <Alert
            className="vl-contract-callout"
            type="info"
            showIcon
            message="检测与识别两阶段"
            description="OCR 算法需要分别选择文字检测模型和文字识别模型，输出文字、置信度与位置，可连接文字条件节点。"
          />
        ) : (
          <Alert
            className="cascade-contract-callout"
            type="warning"
            showIcon
            message="检测数据流与判定规则分离"
            description="蓝色连线传递画面或目标区域，橙色连线组合存在、不存在和数量条件；模型失败不会被取反为告警。"
          />
        )}
      </div>
    );
  };

  const renderStep2 = () => {
    if (algorithmType === 'cascade') {
      return (
        <div className="config-form cascade-config-form">
          <CascadeEditor
            models={models}
            value={cascadeConfig}
            onChange={setCascadeConfig}
          />
        </div>
      );
    }
    if (algorithmType === 'vl') {
      return (
        <div className="config-form vl-config-form">
          <Form form={form} layout="vertical">
            <Card title={<Space><ApiOutlined />接口与认证</Space>} className="config-card">
              <Row gutter={16}>
                <Col xs={24} lg={12}>
                  <Form.Item
                    label="Base URL"
                    name="vlBaseUrl"
                    rules={[
                      { required: true, message: '请输入 VL API Base URL' },
                      { type: 'url', message: '请输入完整的 HTTP(S) 地址' },
                    ]}
                    extra="可填写到 /v1，系统会自动补全 /chat/completions"
                  >
                    <Input placeholder="https://api.example.com/v1" />
                  </Form.Item>
                </Col>
                <Col xs={24} lg={12}>
                  <Form.Item
                    label="API Key"
                    name="vlApiKey"
                    rules={editingAlgorithm?.vl_config?.api_key_configured
                      ? []
                      : [{ required: true, message: '请输入 API Key' }]}
                    extra={editingAlgorithm?.vl_config?.api_key_configured
                      ? '密钥已保存；留空将保留原值'
                      : '密钥只写保存，保存后不会再回传明文'}
                  >
                    <Input.Password autoComplete="new-password" placeholder="sk-..." />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item
                label="附加请求 Headers"
                name="vlExtraHeaders"
                rules={[{ validator: validateJsonObject }]}
                extra="用于租户标识等额外认证信息；Authorization 默认使用 API Key"
              >
                <TextArea rows={4} className="json-editor" placeholder='{"X-Tenant":"demo"}' />
              </Form.Item>
            </Card>

            <Card title={<Space><RobotOutlined />模型参数</Space>} className="config-card">
              <Row gutter={16}>
                <Col xs={24} lg={12}>
                  <Form.Item label="模型名称" name="vlModelName" rules={[{ required: true, message: '请输入模型名称' }]}>
                    <Input placeholder="例如：qwen-vl-max" />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={8} lg={4}>
                  <Form.Item label="温度" name="vlTemperature">
                    <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={8} lg={4}>
                  <Form.Item label="最大 Token" name="vlMaxTokens">
                    <InputNumber min={1} max={32768} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={8} lg={4}>
                  <Form.Item label="图片精度" name="vlImageDetail">
                    <Select>
                      <Option value="auto">自动</Option>
                      <Option value="low">低</Option>
                      <Option value="high">高</Option>
                    </Select>
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col xs={24} lg={8}>
                  <Form.Item label="接口超时（秒）" name="vlTimeoutSeconds">
                    <InputNumber min={1} max={300} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} lg={16}>
                  <Form.Item
                    label="附加请求参数"
                    name="vlExtraBody"
                    rules={[{ validator: validateJsonObject }]}
                    extra="会合并到请求体顶层，可用于 seed 等供应商扩展参数"
                  >
                    <TextArea rows={3} className="json-editor" placeholder='{"seed":7}' />
                  </Form.Item>
                </Col>
              </Row>
            </Card>

            <Card title={<Space><SettingOutlined />判断提示词</Space>} className="config-card vl-prompt-card">
              <Form.Item
                name="vlPromptTemplate"
                rules={[{ required: true, message: '请输入判断提示词' }]}
                extra="可用变量：{workflow_name}、{source_name}、{source_code}、{frame_width}、{frame_height}、{upstream_results_json}、{roi_regions_json}"
              >
                <TextArea rows={10} placeholder={DEFAULT_VL_PROMPT} />
              </Form.Item>
              <Alert
                type="success"
                showIcon
                message="返回格式由系统约束"
                description="模型必须返回 has_detection、detections 和 reason。每个检测项包含名称、置信度和可为空的 bbox。"
              />
            </Card>
          </Form>
        </div>
      );
    }

    if (algorithmType === 'ocr') {
      const detectionModels = models.filter(model =>
        model.enabled && model.model_type === 'OCR' && model.model_role === 'detection');
      const recognitionModels = models.filter(model =>
        model.enabled && model.model_type === 'OCR' && model.model_role === 'recognition');
      return (
        <div className="config-form ocr-config-form">
          <Form form={form} layout="vertical">
            {!ocrRuntimeAvailable ? (
              <Alert
                type="error"
                showIcon
                message="当前环境不支持 OCR"
                description={ocrRuntimeError || '请使用包含 PaddleOCR 运行时的 CPU 或 CUDA 镜像。'}
                style={{ marginBottom: 16 }}
              />
            ) : null}
            <Card title={<Space><FileSearchOutlined />OCR 模型</Space>} className="config-card">
              <Row gutter={16}>
                <Col xs={24} lg={12}>
                  <Form.Item
                    label="文字检测模型"
                    name="ocrDetectionModelId"
                    rules={[{ required: true, message: '请选择文字检测模型' }]}
                  >
                    <Select placeholder="选择 detection 模型">
                      {detectionModels.map(model => (
                        <Option key={model.id} value={model.id}>{model.name} · {model.version}</Option>
                      ))}
                    </Select>
                  </Form.Item>
                </Col>
                <Col xs={24} lg={12}>
                  <Form.Item
                    label="文字识别模型"
                    name="ocrRecognitionModelId"
                    rules={[{ required: true, message: '请选择文字识别模型' }]}
                  >
                    <Select placeholder="选择 recognition 模型">
                      {recognitionModels.map(model => (
                        <Option key={model.id} value={model.id}>{model.name} · {model.version}</Option>
                      ))}
                    </Select>
                  </Form.Item>
                </Col>
              </Row>
              {detectionModels.length === 0 || recognitionModels.length === 0 ? (
                <Alert
                  type="warning"
                  showIcon
                  message="OCR 模型不完整"
                  description="请先在模型管理中分别上传 detection 和 recognition 角色的 OCR 模型。"
                />
              ) : null}
            </Card>

            <Card title={<Space><ThunderboltOutlined />推理参数</Space>} className="config-card">
              <Row gutter={16}>
                <Col xs={24} md={8}>
                  <Form.Item label="运行设备" name="ocrDevice" initialValue="auto">
                    <Select>
                      <Option value="auto">自动选择</Option>
                      <Option value="cpu">CPU</Option>
                      <Option value="gpu">GPU</Option>
                    </Select>
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item
                    label="最低文字置信度"
                    name="ocrRecognitionScoreThreshold"
                    initialValue={0.5}
                    rules={[{ required: true, message: '请输入置信度阈值' }]}
                  >
                    <InputNumber min={0} max={1} step={0.05} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item label="识别批大小" name="ocrRecognitionBatchSize" initialValue={1}>
                    <InputNumber min={1} max={64} step={1} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>
            </Card>

            <Card title={<Space><SettingOutlined />高级检测参数</Space>} className="config-card">
              <Alert
                type="info"
                showIcon
                message="留空时使用模型默认值"
                style={{ marginBottom: 16 }}
              />
              <Row gutter={16}>
                <Col xs={24} md={12} lg={6}>
                  <Form.Item label="检测阈值" name="ocrDetectionThreshold">
                    <InputNumber min={0} max={1} step={0.05} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12} lg={6}>
                  <Form.Item label="文本框阈值" name="ocrBoxThreshold">
                    <InputNumber min={0} max={1} step={0.05} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12} lg={6}>
                  <Form.Item label="文本框扩展比例" name="ocrUnclipRatio">
                    <InputNumber min={0.1} max={10} step={0.1} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12} lg={6}>
                  <Form.Item label="检测边长限制" name="ocrLimitSideLen">
                    <InputNumber min={32} max={4096} step={32} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>
            </Card>
          </Form>
        </div>
      );
    }

    return (
      <>
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
            <Form form={form} layout="vertical">
              {Object.entries(configSchema).map(([key, field]) => (
                <Form.Item
                  key={key}
                  name={field.type === 'model_list' ? undefined : `config_${key}`}
                  label={
                    <Space>
                      {field.label || key}
                      {field.required && <span className="required">*</span>}
                    </Space>
                  }
                  extra={field.description}
                  rules={field.required && field.type !== 'model_list' ? [{ required: true, message: `请填写${field.label || key}` }] : []}
                  initialValue={field.type !== 'model_list' ? field.default : undefined}
                >
                  {renderConfigField(key, field)}
                </Form.Item>
              ))}
            </Form>
          )}
        </div>
      </>
    );
  };

  const renderConfigField = (key: string, field: any) => {
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
            min={field.min}
            max={field.max}
            step={field.step || (field.type === 'int' ? 1 : 0.01)}
            style={{ width: '100%' }}
          />
        );

      case 'boolean':
        return (
          <Switch />
        );

      case 'select':
        return (
          <Select>
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
            style={{ width: 100 }}
          />
        );

      case 'int_list':
        return (
          <Input
            placeholder={field.placeholder || '例如: [0, 1, 2]'}
          />
        );

      default:
        return (
          <Input
            placeholder={field.placeholder}
          />
        );
    }
  };

  const renderModelItemFields = (fieldKey: string, itemSchema: any, itemId: string) => {
    return Object.entries(itemSchema).map(([subKey, subField]: [string, any]) => {
      const fieldId = `model_${fieldKey}_${itemId}_${subKey}`;
      const defaultValue = subField.default !== undefined ? subField.default : '';

      if (subField.type === 'color') {
        return (
          <div key={fieldId} style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', marginBottom: 4, color: 'rgba(0, 0, 0, 0.85)' }}>
              {subField.label || subKey}
            </label>
            <Form.Item name={fieldId} initialValue={defaultValue} style={{ margin: 0 }}>
              <Input
                type="color"
                style={{ width: 80, height: 32, padding: 2, cursor: 'pointer' }}
              />
            </Form.Item>
          </div>
        );
      }

      return (
        <Form.Item
          key={fieldId}
          name={fieldId}
          label={subField.label || subKey}
          initialValue={defaultValue}
          extra={subKey === 'label_name'
            ? <>支持 <code>{'{class}'}</code> 占位符，将替换为实际识别类别</>
            : undefined}
          style={{ marginBottom: 12 }}
        >
          {subField.type === 'model_select' ? (
            <Select
              placeholder="选择模型..."
            >
              {models.filter(m => m.enabled).map(m => (
                <Option key={m.id} value={m.id}>{m.name} ({m.model_type})</Option>
              ))}
            </Select>
          ) : subField.type === 'int' ? (
            <InputNumber
              min={subField.min}
              max={subField.max}
              step={subField.step || 1}
              style={{ width: '100%' }}
            />
          ) : subField.type === 'float' || subField.type === 'number' ? (
            <InputNumber
              min={subField.min}
              max={subField.max}
              step={subField.step || 0.01}
              style={{ width: '100%' }}
            />
          ) : (
            <Input
              placeholder={subField.placeholder || (subKey === 'label_name' ? '例如：目标-{class}' : undefined)}
            />
          )}
        </Form.Item>
      );
    });
  };

  const renderStep3 = () => {
    return (
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
            <Form.Item label="算法描述" name="algorithmDescription">
              <TextArea rows={3} placeholder="说明这个算法识别什么场景，便于在工作流中选择" />
            </Form.Item>
          </Card>

          {algorithmType === 'script' ? (
            <Card title={<Space><ThunderboltOutlined />性能配置</Space>} className="config-card">
              <Row gutter={16}>
                <Col xs={24} md={12}>
                  <Form.Item
                    label="运行超时（秒）"
                    name="runtimeTimeout"
                    initialValue={30}
                  >
                    <InputNumber min={1} max={300} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
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
          ) : null}

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

          {algorithmType !== 'cascade' ? (
          <Card title={<Space><SettingOutlined />显示标签</Space>} className="config-card">
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  label="标签名称"
                  name="labelName"
                  initialValue="Object"
                  extra={<>支持 <code>{'{class}'}</code> 占位符，将替换为实际识别类别，例如：目标-{'{class}'}</>}
                >
                  <Input placeholder="例如：目标-{class}" />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  label="标签颜色"
                  name="labelColor"
                  initialValue="#FF0000"
                >
                  <Input
                    type="color"
                    style={{ width: 100, height: 32, padding: 2, cursor: 'pointer' }}
                  />
                </Form.Item>
              </Col>
            </Row>
          </Card>
          ) : null}
        </Form>
    );
  };

  const steps = [
    {
      title: '选择类型',
      icon: <ApiOutlined className="wizard-step-icon" />,
      description: '选择脚本、组合检测、VL 或 OCR 算法',
    },
    {
      title: '配置参数',
      icon: <SettingOutlined className="wizard-step-icon" />,
      description: algorithmType === 'vl'
        ? '配置接口、模型与提示词'
        : algorithmType === 'ocr'
          ? '配置 OCR 模型与推理参数'
          : algorithmType === 'cascade'
            ? '在画布中连接检测数据流与判定规则'
          : '配置检测器参数',
    },
    {
      title: '执行配置',
      icon: <ControlOutlined className="wizard-step-icon" />,
      description: '配置执行和告警参数',
    },
  ];

  return (
    <Spin spinning={loading}>
      <div className="algorithm-wizard-page">
        <div className="wizard-header">
          <h1>{editingAlgorithm ? '编辑算法' : '创建算法'}</h1>
          <Button onClick={handleCancel}>返回</Button>
        </div>

        <Steps
          current={currentStep}
          items={steps.map((step, index) => ({
            title: step.title,
            description: step.description,
            icon: index < currentStep ? <CheckOutlined className="wizard-step-icon" /> : step.icon,
          }))}
          className="wizard-steps"
        />

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
                {editingAlgorithm ? '保存修改' : '创建算法'}
              </Button>
            )}
          </Space>
        </div>

        {currentStep === 0 && renderStep1()}
        {currentStep === 1 && renderStep2()}
        {currentStep === 2 && renderStep3()}
      </div>
    </Spin>
  );
}
