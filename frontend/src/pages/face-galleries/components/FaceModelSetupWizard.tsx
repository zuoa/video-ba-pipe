import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Card,
  Col,
  Collapse,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Result,
  Row,
  Select,
  Steps,
  Switch,
  Tag,
  Upload,
} from 'antd';
import type { UploadFile } from 'antd/es/upload/interface';
import {
  CheckCircleFilled,
  ClockCircleOutlined,
  CloseCircleFilled,
  InboxOutlined,
  LoadingOutlined,
  SafetyCertificateOutlined,
  ScanOutlined,
} from '@ant-design/icons';
import AppButton from '@/components/common/AppButton';
import {
  createFaceModelBundle,
  getFaceRuntime,
  uploadFaceModelArtifact,
  type FaceModelBundle,
  type FaceRuntimeStatus,
} from '@/services/api';
import './FaceModelSetupWizard.css';

type TaskStatus = 'idle' | 'running' | 'success' | 'error';

interface SetupProgress {
  bundle: TaskStatus;
  detection: TaskStatus;
  embedding: TaskStatus;
  verify: TaskStatus;
}

interface RuntimeProfile {
  backend: string;
  storedRuntime: 'onnxruntime' | 'tensorrt' | 'rknn' | 'torchscript';
  platformLabel: string;
  backendLabel: string;
  backendNote: string;
  architecture: string;
  device: string;
  accept: string;
  extensionLabel: string;
}

interface FaceModelSetupWizardProps {
  open: boolean;
  runtime?: FaceRuntimeStatus;
  repairBundle?: FaceModelBundle;
  onCancel: () => void;
  onComplete: () => Promise<void> | void;
  onCreateGallery?: () => void;
}

const EMPTY_PROGRESS: SetupProgress = {
  bundle: 'idle',
  detection: 'idle',
  embedding: 'idle',
  verify: 'idle',
};

const BUILTIN_BACKENDS = ['rknn', 'tensorrt', 'onnxruntime-cuda', 'onnxruntime', 'torchscript'];

function errorText(error: any, fallback: string) {
  return error?.response?.data?.error || error?.data?.error || error?.message || fallback;
}

function normalizedArchitecture(machine?: string) {
  const value = String(machine || '').toLowerCase();
  if (value === 'x86_64' || value === 'amd64') return 'amd64';
  if (value === 'aarch64' || value === 'arm64') return 'arm64';
  return value || 'any';
}

function runtimeProfile(runtime?: FaceRuntimeStatus): RuntimeProfile | undefined {
  if (!runtime) return undefined;
  const capabilities = runtime.capabilities;
  const available = new Set(capabilities.available_runtimes || []);
  const preferred = capabilities.preferred_backend;
  const backend = [preferred, ...BUILTIN_BACKENDS].find(
    (candidate): candidate is string => Boolean(candidate && available.has(candidate)),
  );
  if (!backend) return undefined;

  const architecture = normalizedArchitecture(capabilities.machine);
  const platformLabel = capabilities.is_rockchip
    ? `Rockchip · ${architecture}`
    : capabilities.is_jetson
      ? `NVIDIA Jetson · ${architecture}`
      : capabilities.onnx_providers.includes('CUDAExecutionProvider') || capabilities.torch_cuda_available
        ? `NVIDIA CUDA · ${architecture}`
        : `CPU · ${architecture}`;

  if (backend === 'rknn') {
    return {
      backend,
      storedRuntime: 'rknn',
      platformLabel,
      backendLabel: 'RKNNLite · NPU 加速',
      backendNote: '当前设备将直接使用 RKNN 制品。以后可在同一模型包中追加 ONNX 等其他平台制品。',
      architecture,
      device: 'rk3588',
      accept: '.rknn',
      extensionLabel: 'RKNN（.rknn）',
    };
  }
  if (backend === 'tensorrt') {
    return {
      backend,
      storedRuntime: 'tensorrt',
      platformLabel,
      backendLabel: 'TensorRT · GPU 加速',
      backendNote: '上传 ONNX，由 TensorRT Execution Provider 在当前 NVIDIA 设备上执行。',
      architecture,
      device: capabilities.is_jetson ? 'jetson' : 'cuda',
      accept: '.onnx',
      extensionLabel: 'ONNX（.onnx）',
    };
  }
  if (backend === 'onnxruntime-cuda' || backend === 'onnxruntime') {
    const cuda = backend === 'onnxruntime-cuda';
    return {
      backend,
      storedRuntime: 'onnxruntime',
      platformLabel,
      backendLabel: cuda ? 'ONNX Runtime · CUDA 加速' : 'ONNX Runtime · CPU',
      backendNote: cuda
        ? '“onnxruntime”是模型格式运行时；当前实际使用 CUDAExecutionProvider，并非 CPU 推理。'
        : '同一份通用 ONNX 制品可在安装了 CUDA Provider 的设备上自动切换为 GPU 加速。',
      architecture: 'any',
      device: 'any',
      accept: '.onnx',
      extensionLabel: 'ONNX（.onnx）',
    };
  }
  return {
    backend,
    storedRuntime: 'torchscript',
    platformLabel,
    backendLabel: capabilities.torch_cuda_available ? 'TorchScript · CUDA 加速' : 'TorchScript · CPU',
    backendNote: '上传由 torch.jit 导出的模型；模型本身应避免写死导出设备。',
    architecture,
    device: 'any',
    accept: '.pt,.pth',
    extensionLabel: 'TorchScript（.pt / .pth）',
  };
}

function stripExtension(filename: string) {
  return filename.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim();
}

function formatFileSize(bytes?: number) {
  if (!bytes) return '';
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function statusIcon(status: TaskStatus) {
  if (status === 'running') return <LoadingOutlined spin />;
  if (status === 'success') return <CheckCircleFilled className="is-success" />;
  if (status === 'error') return <CloseCircleFilled className="is-error" />;
  return <ClockCircleOutlined />;
}

const FaceModelSetupWizard: React.FC<FaceModelSetupWizardProps> = ({
  open,
  runtime,
  repairBundle,
  onCancel,
  onComplete,
  onCreateGallery,
}) => {
  const [form] = Form.useForm();
  const [step, setStep] = useState(0);
  const [detectionFiles, setDetectionFiles] = useState<UploadFile[]>([]);
  const [embeddingFiles, setEmbeddingFiles] = useState<UploadFile[]>([]);
  const [createdBundleId, setCreatedBundleId] = useState<number>();
  const [progress, setProgress] = useState<SetupProgress>(EMPTY_PROGRESS);
  const [submitting, setSubmitting] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [submitError, setSubmitError] = useState<string>();
  const profile = useMemo(() => runtimeProfile(runtime), [runtime]);

  useEffect(() => {
    if (!open) return;
    const dimension = repairBundle?.embedding_dimension || 512;
    form.resetFields();
    form.setFieldsValue({
      name: repairBundle?.name || '',
      version: repairBundle?.version || 'v1.0',
      contract_id: repairBundle?.contract_id || `face-embedding-${dimension}-v1`,
      embedding_dimension: dimension,
      license_name: repairBundle?.license_name || '',
      commercial_use_allowed: repairBundle?.commercial_use_allowed || false,
      detection_input_size: '320x320',
      detection_output_format: 'combined',
      coordinates_are_absolute: true,
    });
    setStep(0);
    setDetectionFiles([]);
    setEmbeddingFiles([]);
    setCreatedBundleId(repairBundle?.id);
    setProgress(repairBundle
      ? { ...EMPTY_PROGRESS, bundle: 'success' }
      : EMPTY_PROGRESS);
    setSubmitting(false);
    setCompleted(false);
    setSubmitError(undefined);
  }, [form, open, repairBundle]);

  useEffect(() => {
    if (!open || repairBundle || form.isFieldTouched('name')) return;
    const detection = detectionFiles[0]?.name;
    const embedding = embeddingFiles[0]?.name;
    if (detection && embedding) {
      form.setFieldValue('name', `${stripExtension(detection)} + ${stripExtension(embedding)}`.slice(0, 80));
    }
  }, [detectionFiles, embeddingFiles, form, open, repairBundle]);

  const ensureFileExtension = (file: UploadFile | undefined, role: string) => {
    if (!file || !profile) return;
    const extensions = profile.accept.split(',').map((item) => item.trim().toLowerCase());
    if (!extensions.some((extension) => file.name.toLowerCase().endsWith(extension))) {
      throw new Error(`${role}文件必须是 ${profile.extensionLabel}`);
    }
  };

  const goNext = async () => {
    setSubmitError(undefined);
    if (step === 0) {
      if (!profile) {
        setSubmitError('当前服务没有检测到可用的人脸推理运行时，请先检查运行环境。');
        return;
      }
      try {
        if (!detectionFiles.length || !embeddingFiles.length) {
          throw new Error('请同时选择人脸检测模型和特征提取模型。');
        }
        ensureFileExtension(detectionFiles[0], '人脸检测');
        ensureFileExtension(embeddingFiles[0], '特征提取');
        setStep(1);
      } catch (error: any) {
        setSubmitError(error.message);
      }
      return;
    }
    try {
      if (!repairBundle) {
        await form.validateFields(['name', 'version', 'embedding_dimension', 'license_name']);
      }
      await form.validateFields([
        'contract_id',
        'detection_input_size',
        'detection_output_format',
      ]);
      setStep(2);
    } catch {
      // Ant Design renders the field-level validation messages.
    }
  };

  const artifactData = (
    role: 'detection' | 'embedding',
    file: File,
    values: Record<string, any>,
  ) => {
    if (!profile) throw new Error('当前平台没有可用推理运行时');
    const metadata = role === 'detection'
      ? {
          input_shape: values.detection_input_size,
          input_layout: 'nchw',
          input_dtype: 'float32',
          color: 'rgb',
          mean: [127.5, 127.5, 127.5],
          std: [128, 128, 128],
          output_format: values.detection_output_format,
          coordinates_are_absolute: values.coordinates_are_absolute,
          ...(values.detection_output_format === 'separate'
            ? { output_indexes: { boxes: 0, scores: 1, landmarks: 2 } }
            : {}),
        }
      : {
          input_shape: '1x3x112x112',
          input_layout: 'nchw',
          input_dtype: 'float32',
          color: 'rgb',
          mean: [127.5, 127.5, 127.5],
          std: [127.5, 127.5, 127.5],
          batch_size: 1,
        };
    const data = new FormData();
    data.append('file', file);
    data.append('role', role);
    data.append('runtime', profile.storedRuntime);
    data.append('architecture', profile.architecture);
    data.append('device', profile.device);
    data.append('metadata', JSON.stringify(metadata));
    return data;
  };

  const submit = async () => {
    if (!profile) return;
    const detectionFile = detectionFiles[0]?.originFileObj;
    const embeddingFile = embeddingFiles[0]?.originFileObj;
    if (!detectionFile || !embeddingFile) {
      setStep(0);
      setSubmitError('模型文件已失效，请重新选择两个模型文件。');
      return;
    }
    const values = form.getFieldsValue(true);
    setSubmitting(true);
    setSubmitError(undefined);
    setProgress((current) => ({
      bundle: current.bundle === 'success' ? 'success' : 'running',
      detection: 'running',
      embedding: 'running',
      verify: 'idle',
    }));

    try {
      let bundleId = createdBundleId;
      if (!bundleId) {
        try {
          const response = await createFaceModelBundle({
            name: values.name,
            version: values.version,
            contract_id: values.contract_id,
            embedding_dimension: values.embedding_dimension,
            input_size: '112x112',
            license_name: values.license_name,
            commercial_use_allowed: values.commercial_use_allowed,
            enabled: true,
          });
          bundleId = response.bundle.id;
          setCreatedBundleId(bundleId);
          setProgress((current) => ({ ...current, bundle: 'success' }));
        } catch (error) {
          setProgress((current) => ({
            ...current,
            bundle: 'error',
            detection: 'idle',
            embedding: 'idle',
          }));
          throw error;
        }
      }

      const uploadRole = async (role: 'detection' | 'embedding', file: File) => {
        try {
          await uploadFaceModelArtifact(bundleId as number, artifactData(role, file, values));
          setProgress((current) => ({ ...current, [role]: 'success' }));
        } catch (error) {
          setProgress((current) => ({ ...current, [role]: 'error' }));
          throw error;
        }
      };
      const uploadResults = await Promise.allSettled([
        uploadRole('detection', detectionFile),
        uploadRole('embedding', embeddingFile),
      ]);
      const uploadErrors = uploadResults
        .map((result, index) => result.status === 'rejected'
          ? `${index === 0 ? '检测模型' : '特征模型'}：${errorText(result.reason, '上传失败')}`
          : undefined)
        .filter(Boolean);
      if (uploadErrors.length) {
        throw new Error(uploadErrors.join('；'));
      }

      setProgress((current) => ({ ...current, verify: 'running' }));
      try {
        const latestRuntime = await getFaceRuntime();
        const currentBundle = latestRuntime.bundles.find((item) => item.bundle_id === bundleId);
        if (!currentBundle?.ready) {
          throw new Error(currentBundle?.error || '服务尚未识别到完整的检测/特征制品组合');
        }
        setProgress((current) => ({ ...current, verify: 'success' }));
      } catch (error) {
        setProgress((current) => ({ ...current, verify: 'error' }));
        throw error;
      }

      await onComplete();
      setCompleted(true);
    } catch (error: any) {
      setSubmitError(errorText(error, '模型配置失败，请检查文件后重试。'));
    } finally {
      setSubmitting(false);
    }
  };

  const values = form.getFieldsValue(true);
  const completedTasks = Object.values(progress).filter((status) => status === 'success').length;
  const progressPercent = Math.round((completedTasks / 4) * 100);

  const uploadCard = (
    role: 'detection' | 'embedding',
    files: UploadFile[],
    setFiles: React.Dispatch<React.SetStateAction<UploadFile[]>>,
  ) => {
    const detection = role === 'detection';
    return (
      <Card className="face-model-wizard__upload" bordered={false}>
        <div className="face-model-wizard__role">
          <span>{detection ? '01' : '02'}</span>
          <div>
            <strong>{detection ? '人脸检测模型' : '特征提取模型'}</strong>
            <small>{detection ? '定位人脸框与五个关键点' : `输出 ${form.getFieldValue('embedding_dimension') || 512} 维人脸特征`}</small>
          </div>
          <Tag color={files.length ? 'success' : 'default'}>{files.length ? '已选择' : '必需'}</Tag>
        </div>
        <Upload.Dragger
          accept={profile?.accept}
          maxCount={1}
          fileList={files}
          beforeUpload={() => false}
          onChange={({ fileList }) => setFiles(fileList.slice(-1))}
          disabled={!profile || submitting}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="face-model-wizard__drop-title">拖入{detection ? '检测' : '特征'}模型，或点击选择</p>
          <p className="face-model-wizard__drop-hint">
            {profile?.extensionLabel || '等待识别当前平台'}
          </p>
        </Upload.Dragger>
        {files[0] ? (
          <small className="face-model-wizard__file-size">{formatFileSize(files[0].size)}</small>
        ) : null}
      </Card>
    );
  };

  const stepContent = () => {
    if (step === 0) {
      return (
        <>
          {profile ? (
            <div className="face-model-wizard__platform">
              <div className="face-model-wizard__platform-icon"><ScanOutlined /></div>
              <div>
                <span>已自动识别当前平台</span>
                <strong>{profile.platformLabel}</strong>
                <small>{profile.backendNote}</small>
              </div>
              <Tag color="cyan">{profile.backendLabel}</Tag>
            </div>
          ) : (
            <Alert
              type="error"
              showIcon
              message="没有检测到可用推理运行时"
              description="请检查 ONNX Runtime、CUDA、RKNNLite 或 Torch 的安装状态后刷新页面。"
            />
          )}
          <Alert
            className="face-model-wizard__notice"
            type="info"
            showIcon
            message="需要上传两个模型文件，系统不会自动下载权重"
            description="模型权重涉及许可证与部署体积，请使用你已获得授权的检测模型和特征模型。两个文件必须来自同一套特征契约。"
          />
          <Row gutter={[16, 16]}>
            <Col xs={24} md={12}>{uploadCard('detection', detectionFiles, setDetectionFiles)}</Col>
            <Col xs={24} md={12}>{uploadCard('embedding', embeddingFiles, setEmbeddingFiles)}</Col>
          </Row>
          <div className="face-model-wizard__contract-hint">
            <SafetyCertificateOutlined />
            <span>
              检测模型需输出<strong>已解码</strong>的人脸框、置信度和 5 个关键点；特征模型固定接收 112×112 对齐人脸。
            </span>
          </div>
        </>
      );
    }

    if (step === 1) {
      return (
        <Form form={form} layout="vertical" requiredMark="optional">
          {repairBundle ? (
            <Alert
              className="face-model-wizard__notice"
              type="info"
              showIcon
              message={`继续配置 ${repairBundle.name} · ${repairBundle.version}`}
              description="向导会沿用已有模型契约，并为当前平台补齐或替换一组检测/特征制品。"
            />
          ) : (
            <Row gutter={16}>
              <Col xs={24} md={15}>
                <Form.Item
                  name="name"
                  label="模型名称"
                  rules={[{ required: true, message: '请输入便于识别的模型名称' }]}
                  extra="已根据文件名自动填写，可以改成团队熟悉的名称。"
                >
                  <Input maxLength={100} placeholder="例如：园区人脸识别模型" />
                </Form.Item>
              </Col>
              <Col xs={24} md={9}>
                <Form.Item name="version" label="版本" rules={[{ required: true, message: '请输入版本' }]}>
                  <Input maxLength={32} placeholder="v1.0" />
                </Form.Item>
              </Col>
            </Row>
          )}
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item
                name="embedding_dimension"
                label="特征维度"
                rules={[{ required: true, message: '请输入特征模型输出维度' }]}
                extra="常见 ArcFace / MobileFaceNet 模型为 512。"
              >
                <InputNumber min={32} max={4096} step={32} disabled={Boolean(repairBundle)} />
              </Form.Item>
            </Col>
            {!repairBundle ? (
              <Col xs={24} md={12}>
                <Form.Item name="license_name" label="权重来源 / 许可证" extra="建议填写，便于上线前审计授权。">
                  <Input maxLength={120} placeholder="例如：内部训练 / Apache-2.0" />
                </Form.Item>
              </Col>
            ) : null}
          </Row>
          {!repairBundle ? (
            <Form.Item name="commercial_use_allowed" label="已确认许可证允许商用" valuePropName="checked">
              <Switch />
            </Form.Item>
          ) : null}
          <Collapse
            ghost
            className="face-model-wizard__advanced"
            items={[{
              key: 'advanced',
              label: '兼容设置（高级）',
              children: (
                <>
                  <Form.Item
                    name="contract_id"
                    label="特征契约标识"
                    rules={[{ required: true, message: '请输入特征契约标识' }]}
                    extra="同一契约下的各平台特征必须可直接互相比对。"
                  >
                    <Input disabled={Boolean(repairBundle)} />
                  </Form.Item>
                  <Row gutter={16}>
                    <Col xs={24} md={12}>
                      <Form.Item
                        name="detection_input_size"
                        label="检测输入尺寸"
                        rules={[{ pattern: /^\d+x\d+$/i, message: '请输入如 320x320 的尺寸' }]}
                      >
                        <Input placeholder="320x320" />
                      </Form.Item>
                    </Col>
                    <Col xs={24} md={12}>
                      <Form.Item name="detection_output_format" label="检测输出形式">
                        <Select options={[
                          { value: 'combined', label: '单个数组 N×15（推荐）' },
                          { value: 'separate', label: '框 / 分数 / 关键点三个数组' },
                        ]} />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Form.Item name="coordinates_are_absolute" label="检测坐标已是绝对像素" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                </>
              ),
            }]}
          />
        </Form>
      );
    }

    if (completed) {
      return (
        <Result
          status="success"
          title="当前平台的人脸模型已配置完成"
          subTitle="检测模型与特征模型已成对上传并通过平台组合检查。下一步可以创建人脸库并绑定这个模型包。"
          extra={onCreateGallery ? (
            <AppButton type="primary" tone="info" onClick={onCreateGallery}>
              下一步：创建人脸库
            </AppButton>
          ) : undefined}
        />
      );
    }

    return (
      <div className="face-model-wizard__review">
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="模型包">{values.name} · {values.version}</Descriptions.Item>
          <Descriptions.Item label="当前平台">{profile?.platformLabel}</Descriptions.Item>
          <Descriptions.Item label="推理方式">{profile?.backendLabel}</Descriptions.Item>
          <Descriptions.Item label="检测模型">{detectionFiles[0]?.name}</Descriptions.Item>
          <Descriptions.Item label="特征模型">{embeddingFiles[0]?.name} · {values.embedding_dimension} 维</Descriptions.Item>
        </Descriptions>
        <Alert
          className="face-model-wizard__notice"
          type={values.commercial_use_allowed ? 'success' : 'warning'}
          showIcon
          message={values.commercial_use_allowed ? '已记录商用授权确认' : '当前模型包将标记为“未确认商用”'}
          description={values.commercial_use_allowed
            ? (values.license_name || '请保留模型权重的授权凭据。')
            : '验证环境可以继续；若生产策略要求商用模型，该模型包不会被加载。'}
        />
        <Card className="face-model-wizard__progress" bordered={false}>
          <div className="face-model-wizard__progress-head">
            <strong>{submitting ? '正在配置，请勿关闭窗口' : completedTasks ? '配置进度' : '准备创建并检查'}</strong>
            <Progress percent={progressPercent} size="small" showInfo={false} />
          </div>
          {[
            ['bundle', repairBundle ? '沿用逻辑模型包' : '创建逻辑模型包'],
            ['detection', '上传人脸检测模型'],
            ['embedding', '上传特征提取模型'],
            ['verify', '检查当前平台制品组合'],
          ].map(([key, label]) => (
            <div className="face-model-wizard__progress-row" key={key}>
              {statusIcon(progress[key as keyof SetupProgress])}
              <span>{label}</span>
            </div>
          ))}
        </Card>
      </div>
    );
  };

  const footer = completed
    ? [
        <AppButton key="done" type="primary" tone="success" onClick={onCancel}>
          完成
        </AppButton>,
      ]
    : [
        <AppButton key="cancel" variant="text" disabled={submitting} onClick={onCancel}>
          取消
        </AppButton>,
        step > 0 ? (
          <AppButton key="previous" disabled={submitting} onClick={() => setStep((current) => current - 1)}>
            上一步
          </AppButton>
        ) : null,
        step < 2 ? (
          <AppButton key="next" type="primary" tone="info" disabled={submitting} onClick={() => void goNext()}>
            下一步
          </AppButton>
        ) : (
          <AppButton key="submit" type="primary" tone="info" loading={submitting} onClick={() => void submit()}>
            {createdBundleId && submitError ? '重试配置' : repairBundle ? '补齐并检查' : '创建并检查'}
          </AppButton>
        ),
      ];

  return (
    <Modal
      className="face-model-wizard"
      title={repairBundle ? '继续配置人脸模型' : '快速配置人脸模型'}
      open={open}
      width={860}
      footer={footer}
      maskClosable={!submitting}
      closable={!submitting}
      destroyOnClose
      onCancel={submitting ? undefined : onCancel}
    >
      <Steps
        className="face-model-wizard__steps"
        current={step}
        size="small"
        items={[
          { title: '平台与文件' },
          { title: '模型信息' },
          { title: '检查并创建' },
        ]}
      />
      <div className="face-model-wizard__body">{stepContent()}</div>
      {submitError ? (
        <Alert
          className="face-model-wizard__error"
          type="error"
          showIcon
          message="配置没有完成"
          description={createdBundleId
            ? `${submitError}。已成功的内容会保留，你可以更换文件后直接重试。`
            : submitError}
          aria-live="assertive"
        />
      ) : null}
    </Modal>
  );
};

export default FaceModelSetupWizard;
