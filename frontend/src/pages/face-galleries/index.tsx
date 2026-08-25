import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Card,
  Col,
  Descriptions,
  Divider,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Upload,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { UploadFile } from 'antd/es/upload/interface';
import {
  CloudServerOutlined,
  DeleteOutlined,
  EyeOutlined,
  ExperimentOutlined,
  FileImageOutlined,
  FileZipOutlined,
  FolderOpenOutlined,
  KeyOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  ScanOutlined,
  SearchOutlined,
  UploadOutlined,
  UserAddOutlined,
} from '@ant-design/icons';
import AppButton from '@/components/common/AppButton';
import { PageHeader, useAppConfirm } from '@/components/common';
import {
  calibrateFaceThresholds,
  createFaceGallery,
  createFaceImport,
  createFaceModelBundle,
  createFacePerson,
  deleteFaceGallery,
  deleteFacePerson,
  deleteFaceTemplate,
  getFaceGalleries,
  getFaceEvents,
  getFaceEventSnapshot,
  getFaceImport,
  getFaceModelBundles,
  getFacePersons,
  getFaceRuntime,
  generateFaceEncryptionKey,
  preflightFaceImport,
  updateFaceGallery,
  uploadFaceModelArtifact,
  uploadFaceTemplate,
  type FaceGallery,
  type FaceCalibrationResult,
  type FaceImportJob,
  type FaceImportPreflight,
  type FaceRecognitionEvent,
  type FaceModelBundle,
  type FacePerson,
  type FaceRuntimeStatus,
} from '@/services/api';
import './index.css';

const PLATFORM_STEPS = [
  { key: 'cpu', label: 'x86 CPU', backend: 'ONNX CPU' },
  { key: 'cuda', label: 'x86 CUDA', backend: 'ONNX CUDA / TorchScript' },
  { key: 'jetson', label: 'Jetson', backend: 'TRT / TorchScript' },
  { key: 'rk', label: 'RK3588', backend: 'RKNN' },
] as const;
const PERSON_PAGE_SIZE = 12;

function errorText(error: any, fallback: string) {
  return error?.response?.data?.error || error?.data?.error || error?.message || fallback;
}

function activePlatform(runtime?: FaceRuntimeStatus) {
  if (!runtime) return '';
  if (runtime.capabilities.is_rockchip) return 'rk';
  if (runtime.capabilities.is_jetson) return 'jetson';
  if (runtime.capabilities.onnx_providers.includes('CUDAExecutionProvider')) return 'cuda';
  return 'cpu';
}

const FaceGalleriesPage: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [galleries, setGalleries] = useState<FaceGallery[]>([]);
  const [bundles, setBundles] = useState<FaceModelBundle[]>([]);
  const [persons, setPersons] = useState<FacePerson[]>([]);
  const [events, setEvents] = useState<FaceRecognitionEvent[]>([]);
  const [runtime, setRuntime] = useState<FaceRuntimeStatus>();
  const [selectedGalleryId, setSelectedGalleryId] = useState<number>();
  const [search, setSearch] = useState('');
  const [personPage, setPersonPage] = useState(1);
  const [personTotal, setPersonTotal] = useState(0);
  const [galleryModalOpen, setGalleryModalOpen] = useState(false);
  const [personModalOpen, setPersonModalOpen] = useState(false);
  const [bundleModalOpen, setBundleModalOpen] = useState(false);
  const [artifactModalOpen, setArtifactModalOpen] = useState(false);
  const [templatePerson, setTemplatePerson] = useState<FacePerson>();
  const [templateFiles, setTemplateFiles] = useState<UploadFile[]>([]);
  const [artifactFiles, setArtifactFiles] = useState<UploadFile[]>([]);
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importFiles, setImportFiles] = useState<UploadFile[]>([]);
  const [importPreflight, setImportPreflight] = useState<FaceImportPreflight>();
  const [importJob, setImportJob] = useState<FaceImportJob>();
  const [calibration, setCalibration] = useState<FaceCalibrationResult>();
  const [eventSnapshotUrl, setEventSnapshotUrl] = useState<string>();
  const [saving, setSaving] = useState(false);
  const [generatingKey, setGeneratingKey] = useState(false);
  const [galleryForm] = Form.useForm();
  const [personForm] = Form.useForm();
  const [bundleForm] = Form.useForm();
  const [artifactForm] = Form.useForm();
  const confirmAction = useAppConfirm();

  const selectedGallery = useMemo(
    () => galleries.find((item) => item.id === selectedGalleryId),
    [galleries, selectedGalleryId],
  );
  const importFinished = Boolean(
    importJob && ['completed', 'completed_with_errors', 'failed'].includes(importJob.status),
  );

  const openImportModal = () => {
    if (importFinished) {
      setImportJob(undefined);
      setImportPreflight(undefined);
      setImportFiles([]);
    }
    setImportModalOpen(true);
  };

  const handleGenerateEncryptionKey = async () => {
    setGeneratingKey(true);
    try {
      const response = await generateFaceEncryptionKey();
      setRuntime((current) => current
        ? { ...current, encryption_ready: response.encryption_ready }
        : current);
      message.success(response.created ? '生物数据密钥已生成并持久化' : '生物数据密钥已就绪');
    } catch (error: any) {
      message.error(errorText(error, '生成生物数据密钥失败'));
    } finally {
      setGeneratingKey(false);
    }
  };

  const loadOverview = useCallback(async () => {
    setLoading(true);
    try {
      const [galleryResponse, bundleResponse, runtimeResponse] = await Promise.all([
        getFaceGalleries(),
        getFaceModelBundles(),
        getFaceRuntime(),
      ]);
      const nextGalleries = galleryResponse.galleries || [];
      setGalleries(nextGalleries);
      setBundles(bundleResponse.bundles || []);
      setRuntime(runtimeResponse);
      setSelectedGalleryId((current) =>
        current && nextGalleries.some((item) => item.id === current)
          ? current
          : nextGalleries[0]?.id,
      );
    } catch (error: any) {
      message.error(errorText(error, '无法加载人脸库'));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPersons = useCallback(async () => {
    if (!selectedGalleryId) {
      setPersons([]);
      setPersonTotal(0);
      return;
    }
    try {
      const response = await getFacePersons(
        selectedGalleryId,
        search,
        personPage,
        PERSON_PAGE_SIZE,
      );
      setPersons(response.persons || []);
      setPersonTotal(response.pagination?.total || 0);
      if (response.pagination && response.pagination.page !== personPage) {
        setPersonPage(response.pagination.page);
      }
    } catch (error: any) {
      message.error(errorText(error, '无法加载人员'));
    }
  }, [personPage, search, selectedGalleryId]);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadPersons(), 180);
    return () => window.clearTimeout(timer);
  }, [loadPersons]);

  useEffect(() => {
    if (!selectedGalleryId) {
      setEvents([]);
      return;
    }
    void getFaceEvents(selectedGalleryId)
      .then((response) => setEvents(response.events || []))
      .catch((error) => message.error(errorText(error, '无法加载识别事件')));
  }, [selectedGalleryId]);

  useEffect(() => {
    if (!importJob || ['completed', 'completed_with_errors', 'failed'].includes(importJob.status)) {
      return undefined;
    }
    const timer = window.setInterval(async () => {
      try {
        const response = await getFaceImport(importJob.id);
        setImportJob(response.job);
        if (['completed', 'completed_with_errors', 'failed'].includes(response.job.status)) {
          window.clearInterval(timer);
          await Promise.all([loadPersons(), loadOverview()]);
        }
      } catch (error: any) {
        window.clearInterval(timer);
        message.error(errorText(error, '无法获取导入进度'));
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [importJob?.id, importJob?.status, loadOverview, loadPersons]);

  const submitGallery = async () => {
    const values = await galleryForm.validateFields();
    setSaving(true);
    try {
      const response = await createFaceGallery(values);
      setGalleryModalOpen(false);
      galleryForm.resetFields();
      await loadOverview();
      setPersonPage(1);
      setSelectedGalleryId(response.gallery.id);
      message.success('人脸库已创建');
    } catch (error: any) {
      message.error(errorText(error, '创建人脸库失败'));
    } finally {
      setSaving(false);
    }
  };

  const submitPerson = async () => {
    const values = await personForm.validateFields();
    setSaving(true);
    try {
      await createFacePerson({ ...values, gallery_ids: [selectedGalleryId] });
      setPersonModalOpen(false);
      personForm.resetFields();
      await Promise.all([loadPersons(), loadOverview()]);
      message.success('人员已加入当前人脸库');
    } catch (error: any) {
      message.error(errorText(error, '添加人员失败'));
    } finally {
      setSaving(false);
    }
  };

  const submitTemplate = async () => {
    const file = templateFiles[0]?.originFileObj;
    if (!templatePerson || !file) {
      message.warning('请选择一张只包含单个人脸的照片');
      return;
    }
    setSaving(true);
    try {
      await uploadFaceTemplate(templatePerson.id, file, selectedGalleryId);
      setTemplatePerson(undefined);
      setTemplateFiles([]);
      await Promise.all([loadPersons(), loadOverview()]);
      message.success('人脸照片已通过质量检查并加密录入');
    } catch (error: any) {
      message.error(errorText(error, '人脸照片录入失败'));
    } finally {
      setSaving(false);
    }
  };

  const submitBundle = async () => {
    const values = await bundleForm.validateFields();
    setSaving(true);
    try {
      await createFaceModelBundle(values);
      setBundleModalOpen(false);
      bundleForm.resetFields();
      await loadOverview();
      message.success('逻辑模型包已创建');
    } catch (error: any) {
      message.error(errorText(error, '创建模型包失败'));
    } finally {
      setSaving(false);
    }
  };

  const submitArtifact = async () => {
    const values = await artifactForm.validateFields();
    const file = artifactFiles[0]?.originFileObj;
    if (!file) {
      message.warning('请选择模型制品');
      return;
    }
    const data = new FormData();
    data.append('file', file);
    data.append('role', values.role);
    data.append('runtime', values.runtime);
    data.append('architecture', values.architecture || 'any');
    data.append('device', values.device || 'any');
    data.append('metadata', values.metadata || '{}');
    setSaving(true);
    try {
      await uploadFaceModelArtifact(values.bundle_id, data);
      setArtifactModalOpen(false);
      setArtifactFiles([]);
      artifactForm.resetFields();
      await loadOverview();
      message.success('平台模型制品已上传');
    } catch (error: any) {
      message.error(errorText(error, '上传模型制品失败'));
    } finally {
      setSaving(false);
    }
  };

  const submitImport = async () => {
    const file = importFiles[0]?.originFileObj;
    if (!selectedGalleryId || !file) {
      message.warning('请选择 ZIP 导入包');
      return;
    }
    setSaving(true);
    try {
      if (!importPreflight?.success) {
        const response = await preflightFaceImport(file);
        setImportPreflight(response);
        message.success(`预检通过：${response.person_count} 人，${response.image_count} 张照片`);
      } else {
        const response = await createFaceImport(selectedGalleryId, file);
        setImportJob(response.job);
        message.success('导入任务已提交，可在此查看进度');
      }
    } catch (error: any) {
      const payload = error?.response?.data || error?.data;
      if (payload?.errors) setImportPreflight(payload);
      message.error(errorText(error, '导入包检查失败'));
    } finally {
      setSaving(false);
    }
  };

  const runCalibration = async () => {
    if (!selectedGalleryId) return;
    setSaving(true);
    try {
      setCalibration(await calibrateFaceThresholds(selectedGalleryId));
    } catch (error: any) {
      message.error(errorText(error, '阈值评估失败'));
    } finally {
      setSaving(false);
    }
  };

  const applyCalibration = async () => {
    if (!calibration) return;
    setSaving(true);
    try {
      await updateFaceGallery(calibration.gallery_id, {
        low_threshold: calibration.suggested_low_threshold,
        high_threshold: calibration.suggested_high_threshold,
      });
      setCalibration(undefined);
      await loadOverview();
      message.success('建议阈值已应用');
    } catch (error: any) {
      message.error(errorText(error, '应用阈值失败'));
    } finally {
      setSaving(false);
    }
  };

  const previewEventSnapshot = async (event: FaceRecognitionEvent) => {
    try {
      const blob = await getFaceEventSnapshot(event.id);
      setEventSnapshotUrl(URL.createObjectURL(blob));
    } catch (error: any) {
      message.error(errorText(error, '无法读取加密抓拍'));
    }
  };

  const columns: ColumnsType<FacePerson> = [
    {
      title: '人员',
      key: 'identity',
      render: (_, person) => (
        <div className="face-person-cell">
          <div className="face-person-cell__avatar">{person.name.slice(0, 1)}</div>
          <div>
            <strong>{person.name}</strong>
            <span>{person.person_code}</span>
          </div>
        </div>
      ),
    },
    {
      title: '模板质量',
      key: 'templates',
      width: 220,
      render: (_, person) => {
        const ready = person.ready_template_count;
        const percent = Math.min(100, Math.round((ready / 3) * 100));
        return (
          <div className="template-health">
            <div><span>{ready} 张可用</span><span>建议 3–5 张</span></div>
            <Progress percent={percent} showInfo={false} size="small" strokeColor="#0f766e" />
          </div>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      width: 110,
      render: (enabled) => <Badge status={enabled ? 'success' : 'default'} text={enabled ? '启用' : '停用'} />,
    },
    {
      title: '操作',
      key: 'actions',
      width: 190,
      render: (_, person) => (
        <Space size={4}>
          <AppButton
            size="small"
            tone="info"
            variant="text"
            icon={<FileImageOutlined />}
            onClick={() => setTemplatePerson(person)}
          >
            录入照片
          </AppButton>
          <Tooltip title="删除人员及全部生物模板">
            <AppButton
              size="small"
              tone="danger"
              variant="text"
              iconOnly
              aria-label={`删除${person.name}`}
              icon={<DeleteOutlined />}
              onClick={() => confirmAction({
                title: '删除人员',
                objectName: `${person.name}（${person.person_code}）`,
                description: '人员的全部加密照片和特征将一并删除，此操作无法恢复。',
                onConfirm: async () => {
                  await deleteFacePerson(person.id);
                  await Promise.all([loadPersons(), loadOverview()]);
                  message.success('人员已删除');
                },
              })}
            />
          </Tooltip>
        </Space>
      ),
    },
  ];

  const galleryPanel = (
    <div className="face-workbench">
      <aside className="gallery-ledger" aria-label="人脸库列表">
        <div className="gallery-ledger__heading">
          <div><span>人脸库</span><strong>{galleries.length}</strong></div>
          <AppButton
            iconOnly
            aria-label="创建人脸库"
            icon={<PlusOutlined />}
            onClick={() => setGalleryModalOpen(true)}
          />
        </div>
        <div className="gallery-ledger__list">
          {galleries.map((gallery) => (
            <button
              type="button"
              key={gallery.id}
              className={`gallery-entry ${gallery.id === selectedGalleryId ? 'is-active' : ''}`}
              onClick={() => {
                setPersonPage(1);
                setSelectedGalleryId(gallery.id);
              }}
            >
              <span className="gallery-entry__icon"><FolderOpenOutlined /></span>
              <span className="gallery-entry__copy">
                <strong>{gallery.name}</strong>
                <small>{gallery.person_count} 人 · {gallery.template_count} 模板</small>
              </span>
              <span className="gallery-entry__version">v{gallery.gallery_version}</span>
            </button>
          ))}
          {!galleries.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="先创建一个人脸库" />}
        </div>
      </aside>

      <section className="identity-ledger">
        {selectedGallery ? (
          <>
            <div className="identity-ledger__toolbar">
              <div>
                <span className="identity-ledger__eyebrow">当前名单</span>
                <h2>{selectedGallery.name}</h2>
                <Space size={6} wrap>
                  <Tag color={selectedGallery.enabled ? 'green' : 'default'}>{selectedGallery.enabled ? '识别中' : '已停用'}</Tag>
                  <Tag>{selectedGallery.model_bundle_name || '未配置模型'}</Tag>
                  <Tag>阈值 {selectedGallery.low_threshold.toFixed(2)} / {selectedGallery.high_threshold.toFixed(2)}</Tag>
                </Space>
              </div>
              <Space wrap>
                <Input
                  allowClear
                  prefix={<SearchOutlined />}
                  placeholder="姓名或人员编号"
                  value={search}
                  onChange={(event) => {
                    setPersonPage(1);
                    setSearch(event.target.value);
                  }}
                  className="identity-search"
                />
                <Tooltip title="使用库内同人/异人分数建议灰区和确认阈值">
                  <AppButton
                    iconOnly
                    aria-label="评估识别阈值"
                    icon={<ExperimentOutlined />}
                    loading={saving}
                    disabled={selectedGallery.template_count < 2}
                    onClick={() => void runCalibration()}
                  />
                </Tooltip>
                <AppButton
                  icon={<FileZipOutlined />}
                  onClick={openImportModal}
                  disabled={!selectedGallery.model_bundle_id}
                >
                  批量导入
                </AppButton>
                <AppButton
                  type="primary"
                  tone="info"
                  icon={<UserAddOutlined />}
                  onClick={() => setPersonModalOpen(true)}
                  disabled={!selectedGallery.model_bundle_id}
                >
                  添加人员
                </AppButton>
              </Space>
            </div>
            {!selectedGallery.model_bundle_id ? (
              <Alert
                type="warning"
                showIcon
                message="当前人脸库尚未绑定逻辑模型包"
                description="绑定模型包后才能录入照片和生成跨平台特征。"
              />
            ) : null}
            <Table
              rowKey="id"
              columns={columns}
              dataSource={persons}
              pagination={{
                current: personPage,
                pageSize: PERSON_PAGE_SIZE,
                total: personTotal,
                showSizeChanger: false,
                onChange: setPersonPage,
              }}
              locale={{ emptyText: <Empty description="名单为空，添加第一位人员" /> }}
            />
            <div className="identity-ledger__danger">
              <span>删除名单不会删除同时属于其他名单的人员。</span>
              <AppButton
                tone="danger"
                variant="text"
                icon={<DeleteOutlined />}
                onClick={() => confirmAction({
                  title: '删除人脸库',
                  objectName: selectedGallery.name,
                  description: '将删除该名单关系和识别配置，人员档案仍可由其他名单使用。',
                  onConfirm: async () => {
                    await deleteFaceGallery(selectedGallery.id);
                    await loadOverview();
                    message.success('人脸库已删除');
                  },
                })}
              >
                删除人脸库
              </AppButton>
            </div>
          </>
        ) : (
          <Empty description="选择或创建一个人脸库" />
        )}
      </section>
    </div>
  );

  const modelPanel = (
    <div className="face-model-grid">
      <div className="face-model-grid__toolbar">
        <div>
          <h2>逻辑模型包</h2>
          <p>一个版本管理多平台制品，保证预处理与 512 维特征契约一致。</p>
        </div>
        <Space>
          <AppButton icon={<PlusOutlined />} onClick={() => setBundleModalOpen(true)}>新建模型包</AppButton>
          <AppButton type="primary" tone="info" icon={<UploadOutlined />} onClick={() => setArtifactModalOpen(true)} disabled={!bundles.length}>上传平台制品</AppButton>
        </Space>
      </div>
      <Row gutter={[16, 16]}>
        {bundles.map((bundle) => {
          const runtimeBundle = runtime?.bundles.find((item) => item.bundle_id === bundle.id);
          return (
            <Col xs={24} lg={12} key={bundle.id}>
              <Card className="face-model-card">
                <div className="face-model-card__title">
                  <div><strong>{bundle.name}</strong><span>{bundle.version}</span></div>
                  <Badge status={runtimeBundle?.ready ? 'success' : 'warning'} text={runtimeBundle?.ready ? runtimeBundle.backend : '制品不完整'} />
                </div>
                <Descriptions column={2} size="small">
                  <Descriptions.Item label="契约">{bundle.contract_id}</Descriptions.Item>
                  <Descriptions.Item label="特征">{bundle.embedding_dimension} 维</Descriptions.Item>
                  <Descriptions.Item label="授权">{bundle.commercial_use_allowed ? '允许商用' : '仅验证'}</Descriptions.Item>
                  <Descriptions.Item label="制品">{bundle.artifacts.length} 个</Descriptions.Item>
                </Descriptions>
                <Divider />
                <Space size={[6, 6]} wrap>
                  {bundle.artifacts.map((artifact) => (
                    <Tag key={artifact.id} color={artifact.role === 'detection' ? 'cyan' : 'purple'}>
                      {artifact.runtime} · {artifact.role === 'detection' ? '检测' : '特征'}
                    </Tag>
                  ))}
                </Space>
                {runtimeBundle?.error ? <p className="face-model-card__error">{runtimeBundle.error}</p> : null}
              </Card>
            </Col>
          );
        })}
      </Row>
    </div>
  );

  const eventPanel = (
    <Card className="face-event-ledger" bordered={false}>
      <div className="face-model-grid__toolbar">
        <div>
          <h2>最近识别事件</h2>
          <p>同一跟踪轨迹只记录一次确认结果；抓拍按名单保留策略自动清理。</p>
        </div>
        <Tag>{selectedGallery?.name || '请选择人脸库'}</Tag>
      </div>
      <Table<FaceRecognitionEvent>
        rowKey="id"
        dataSource={events}
        pagination={{ pageSize: 15, showSizeChanger: false }}
        columns={[
          {
            title: '时间',
            dataIndex: 'occurred_at',
            width: 190,
            render: (value) => new Date(value).toLocaleString(),
          },
          {
            title: '判定',
            dataIndex: 'identity_status',
            width: 110,
            render: (value) => <Tag color={value === 'known' ? 'green' : 'orange'}>{value === 'known' ? '白名单' : '陌生人'}</Tag>,
          },
          {
            title: '人员',
            key: 'person',
            render: (_, event) => event.person_name
              ? <span>{event.person_name} <small>{event.person_code}</small></span>
              : <span className="face-event-muted">未匹配</span>,
          },
          {
            title: '相似度 / 阈值',
            key: 'score',
            width: 150,
            render: (_, event) => event.similarity == null
              ? '—'
              : `${event.similarity.toFixed(3)} / ${(event.threshold || 0).toFixed(3)}`,
          },
          {
            title: '推理路径',
            dataIndex: 'inference_backend',
            width: 150,
            render: (value) => value || '—',
          },
          {
            title: '活体',
            dataIndex: 'liveness_status',
            width: 100,
            render: () => <Tag>未检测</Tag>,
          },
          {
            title: '抓拍',
            key: 'snapshot',
            width: 90,
            render: (_, event) => (
              <AppButton
                iconOnly
                variant="text"
                aria-label="查看加密抓拍"
                icon={<EyeOutlined />}
                disabled={!event.snapshot_path}
                onClick={() => void previewEventSnapshot(event)}
              />
            ),
          },
        ]}
        locale={{ emptyText: <Empty description="当前名单暂无确认事件" /> }}
      />
    </Card>
  );

  return (
    <div className="face-page">
      <PageHeader
        icon={<ScanOutlined />}
        eyebrow="IDENTITY REGISTRY"
        title="人脸识别"
        subtitle="维护加密名单、跨平台模型与人员录入质量"
        count={galleries.reduce((total, item) => total + item.person_count, 0)}
        countLabel="人次"
      />

      <section className="runtime-rail" aria-label="推理平台兼容状态">
        <div className="runtime-rail__lead">
          <CloudServerOutlined />
          <div><span>当前推理路径</span><strong>{runtime?.capabilities.preferred_backend || '检测中'}</strong></div>
        </div>
        <div className="runtime-rail__steps">
          {PLATFORM_STEPS.map((step) => {
            const active = step.key === activePlatform(runtime);
            return (
              <div className={`runtime-step ${active ? 'is-active' : ''}`} key={step.key}>
                <span className="runtime-step__dot" />
                <div><strong>{step.label}</strong><small>{step.backend}</small></div>
              </div>
            );
          })}
        </div>
        <div className={`runtime-secret ${runtime?.encryption_ready ? 'is-ready' : ''}`} aria-live="polite">
          <SafetyCertificateOutlined />
          <span>{runtime ? (runtime.encryption_ready ? '生物数据密钥就绪' : '未配置加密密钥') : '密钥状态检测中'}</span>
          {runtime && !runtime.encryption_ready ? (
            <Tooltip title="生成 256 位密钥并保存到持久化数据目录，请随数据卷一起备份">
              <AppButton
                className="runtime-secret__action"
                tone="warning"
                icon={<KeyOutlined />}
                loading={generatingKey}
                aria-label="自动生成生物数据加密密钥"
                onClick={() => void handleGenerateEncryptionKey()}
              >
                自动生成
              </AppButton>
            </Tooltip>
          ) : null}
        </div>
      </section>

      {runtime?.capabilities.plugin_errors?.length ? (
        <Alert
          className="face-runtime-error"
          type="error"
          showIcon
          message="推理插件加载失败"
          description={runtime.capabilities.plugin_errors.join('；')}
        />
      ) : null}

      {loading ? (
        <div className="face-page__loading"><Spin size="large" /></div>
      ) : (
        <Tabs
          defaultActiveKey="galleries"
          items={[
            { key: 'galleries', label: '人员与名单', children: galleryPanel },
            { key: 'events', label: '识别事件', children: eventPanel },
            { key: 'models', label: '跨平台模型', children: modelPanel },
          ]}
        />
      )}

      <Modal title="创建人脸库" open={galleryModalOpen} onCancel={() => setGalleryModalOpen(false)} onOk={submitGallery} confirmLoading={saving} okText="创建人脸库">
        <Form form={galleryForm} layout="vertical" initialValues={{ low_threshold: 0.5, high_threshold: 0.6, enabled: true }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入人脸库名称' }]}><Input placeholder="例如：园区员工" /></Form.Item>
          <Form.Item name="description" label="说明"><Input.TextArea rows={2} placeholder="这份名单在哪些场景使用" /></Form.Item>
          <Form.Item name="model_bundle_id" label="逻辑模型包" rules={[{ required: true, message: '请选择模型包' }]}>
            <Select options={bundles.map((item) => ({ value: item.id, label: `${item.name} · ${item.version}` }))} />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}><Form.Item name="low_threshold" label="低阈值"><InputNumber min={0} max={1} step={0.01} /></Form.Item></Col>
            <Col span={12}><Form.Item name="high_threshold" label="高阈值"><InputNumber min={0} max={1} step={0.01} /></Form.Item></Col>
          </Row>
          <Form.Item name="enabled" label="立即启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>

      <Modal title="添加人员" open={personModalOpen} onCancel={() => setPersonModalOpen(false)} onOk={submitPerson} confirmLoading={saving} okText="添加人员">
        <Form form={personForm} layout="vertical" initialValues={{ enabled: true }}>
          <Form.Item name="person_code" label="人员编号" rules={[{ required: true, message: '请输入人员编号' }]}><Input placeholder="例如：E-1042" /></Form.Item>
          <Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入姓名' }]}><Input /></Form.Item>
          <Form.Item name="enabled" label="参与识别" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`录入人脸 · ${templatePerson?.name || ''}`} open={Boolean(templatePerson)} onCancel={() => { setTemplatePerson(undefined); setTemplateFiles([]); }} onOk={submitTemplate} confirmLoading={saving} okText="检查并录入">
        <Alert type="info" showIcon message="选择清晰正脸照片" description="照片必须只包含一人，建议短边不低于 320px。服务会检查人脸尺寸、清晰度和曝光。" />
        <Upload.Dragger accept="image/jpeg,image/png,image/webp" maxCount={1} fileList={templateFiles} beforeUpload={() => false} onChange={({ fileList }) => setTemplateFiles(fileList)}>
          <p className="ant-upload-drag-icon"><FileImageOutlined /></p>
          <p>拖入照片或点击选择</p>
        </Upload.Dragger>
        {templatePerson?.templates.length ? (
          <List
            className="template-list"
            size="small"
            header="已录入模板"
            dataSource={templatePerson.templates}
            renderItem={(template) => (
              <List.Item actions={[<AppButton key="delete" tone="danger" variant="text" size="small" onClick={async () => { await deleteFaceTemplate(template.id); await Promise.all([loadPersons(), loadOverview()]); }}>删除</AppButton>]}>质量 {Math.round((template.quality_score || 0) * 100)}% · {template.inference_backend}</List.Item>
            )}
          />
        ) : null}
      </Modal>

      <Modal title="新建逻辑模型包" open={bundleModalOpen} onCancel={() => setBundleModalOpen(false)} onOk={submitBundle} confirmLoading={saving} okText="创建模型包">
        <Form form={bundleForm} layout="vertical" initialValues={{ version: 'v1.0', contract_id: 'arcface-mobilefacenet-512-v1', embedding_dimension: 512, input_size: '112x112', commercial_use_allowed: false }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input placeholder="RetinaFace + MobileFaceNet" /></Form.Item>
          <Row gutter={12}><Col span={12}><Form.Item name="version" label="版本" rules={[{ required: true }]}><Input /></Form.Item></Col><Col span={12}><Form.Item name="input_size" label="特征输入"><Input /></Form.Item></Col></Row>
          <Form.Item name="contract_id" label="模型契约" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="embedding_dimension" label="特征维度"><InputNumber min={32} max={4096} /></Form.Item>
          <Form.Item name="license_name" label="模型许可证"><Input placeholder="内部验证时也应记录权重来源" /></Form.Item>
          <Form.Item name="commercial_use_allowed" label="许可证允许商用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`批量导入 · ${galleries.find((item) => item.id === (importJob?.gallery_id || selectedGalleryId))?.name || ''}`}
        open={importModalOpen}
        onCancel={() => {
          setImportModalOpen(false);
          if (importFinished) {
            setImportJob(undefined);
            setImportPreflight(undefined);
            setImportFiles([]);
          }
        }}
        onOk={submitImport}
        confirmLoading={saving}
        okText={importJob ? '导入已提交' : importPreflight?.success ? '开始导入' : '预检导入包'}
        okButtonProps={{ disabled: Boolean(importJob) }}
        cancelText={importJob ? '后台运行' : '取消'}
      >
        {importJob ? (
          <div className="face-import-progress">
            <Progress
              type="circle"
              percent={importJob.total_people
                ? Math.round((importJob.processed_people / importJob.total_people) * 100)
                : 0}
              status={importJob.status === 'failed' ? 'exception' : undefined}
            />
            <div>
              <strong>{importJob.status === 'processing' ? '正在生成加密人脸模板' : importJob.status}</strong>
              <p>{importJob.processed_people} / {importJob.total_people} 人已处理，成功 {importJob.succeeded_people}，失败 {importJob.failed_people}</p>
              {importJob.errors.slice(0, 4).map((item, index) => (
                <small key={`${item.row}-${index}`}>{item.person_code || `第 ${item.row} 行`}：{item.error || item.warning}</small>
              ))}
            </div>
          </div>
        ) : (
          <>
            <Alert
              type="info"
              showIcon
              message="ZIP 内需要 manifest.csv 与 photos/ 目录"
              description="manifest.csv 至少包含 person_code,name；照片放在 photos/{person_code}/ 下，每人最多录入前 5 张。"
            />
            <Upload.Dragger
              accept=".zip,application/zip"
              maxCount={1}
              fileList={importFiles}
              beforeUpload={() => false}
              onChange={({ fileList }) => {
                setImportFiles(fileList);
                setImportPreflight(undefined);
              }}
            >
              <p className="ant-upload-drag-icon"><FileZipOutlined /></p>
              <p>拖入批量导入 ZIP 或点击选择</p>
            </Upload.Dragger>
            {importPreflight ? (
              <Alert
                className="face-import-result"
                type={importPreflight.success ? 'success' : 'error'}
                showIcon
                message={importPreflight.success
                  ? `预检通过：${importPreflight.person_count} 人，${importPreflight.image_count} 张照片`
                  : `预检发现 ${importPreflight.errors.length} 个问题`}
                description={importPreflight.errors.slice(0, 5).map((item) => `第 ${item.row || '?'} 行：${item.error}`).join('；') || undefined}
              />
            ) : null}
          </>
        )}
      </Modal>

      <Modal
        title="阈值离线评估"
        open={Boolean(calibration)}
        onCancel={() => setCalibration(undefined)}
        onOk={applyCalibration}
        confirmLoading={saving}
        okText="应用建议阈值"
      >
        {calibration ? (
          <>
            <Alert
              type="warning"
              showIcon
              message="这是库内估计，不替代现场标定"
              description="上线前仍需使用目标摄像头、距离和光照下的独立正负样本验证 FPIR/FNIR。"
            />
            <Descriptions className="face-calibration" bordered size="small" column={2}>
              <Descriptions.Item label="建议低阈值">{calibration.suggested_low_threshold.toFixed(4)}</Descriptions.Item>
              <Descriptions.Item label="建议高阈值">{calibration.suggested_high_threshold.toFixed(4)}</Descriptions.Item>
              <Descriptions.Item label="估计 FPIR">{(calibration.measured_fpir * 100).toFixed(3)}%</Descriptions.Item>
              <Descriptions.Item label="估计 FNIR">{calibration.measured_fnir == null ? '样本不足' : `${(calibration.measured_fnir * 100).toFixed(2)}%`}</Descriptions.Item>
              <Descriptions.Item label="同人对">{calibration.genuine_pair_count}</Descriptions.Item>
              <Descriptions.Item label="异人对">{calibration.impostor_pair_count}</Descriptions.Item>
            </Descriptions>
          </>
        ) : null}
      </Modal>

      <Modal
        title="加密事件抓拍"
        open={Boolean(eventSnapshotUrl)}
        footer={null}
        onCancel={() => {
          if (eventSnapshotUrl) URL.revokeObjectURL(eventSnapshotUrl);
          setEventSnapshotUrl(undefined);
        }}
      >
        {eventSnapshotUrl ? <img className="face-event-snapshot" src={eventSnapshotUrl} alt="人脸识别事件抓拍" /> : null}
      </Modal>

      <Modal title="上传平台模型制品" open={artifactModalOpen} onCancel={() => setArtifactModalOpen(false)} onOk={submitArtifact} confirmLoading={saving} okText="上传制品">
        <Form form={artifactForm} layout="vertical" initialValues={{ architecture: 'any', device: 'any', metadata: '{}', runtime: 'onnxruntime', role: 'detection' }}>
          <Form.Item name="bundle_id" label="逻辑模型包" rules={[{ required: true }]}><Select options={bundles.map((item) => ({ value: item.id, label: `${item.name} · ${item.version}` }))} /></Form.Item>
          <Row gutter={12}><Col span={12}><Form.Item name="role" label="模型角色"><Select options={[{ value: 'detection', label: '人脸检测' }, { value: 'embedding', label: '特征提取' }]} /></Form.Item></Col><Col span={12}><Form.Item name="runtime" label="运行时"><Select options={[{ value: 'onnxruntime', label: 'ONNX Runtime' }, { value: 'tensorrt', label: 'TensorRT EP' }, { value: 'torchscript', label: 'TorchScript' }, { value: 'rknn', label: 'RKNNLite' }]} /></Form.Item></Col></Row>
          <Row gutter={12}><Col span={12}><Form.Item name="architecture" label="架构"><Input placeholder="any / amd64 / arm64" /></Form.Item></Col><Col span={12}><Form.Item name="device" label="设备"><Input placeholder="any / cuda / rk3588" /></Form.Item></Col></Row>
          <Form.Item name="metadata" label="输入输出元数据" rules={[{ validator: async (_, value) => { try { JSON.parse(value || '{}'); } catch { throw new Error('请输入有效 JSON'); } } }]}><Input.TextArea rows={4} /></Form.Item>
          <Upload accept=".onnx,.rknn,.pt,.pth" maxCount={1} fileList={artifactFiles} beforeUpload={() => false} onChange={({ fileList }) => setArtifactFiles(fileList)}><AppButton icon={<UploadOutlined />}>选择模型文件</AppButton></Upload>
        </Form>
      </Modal>
    </div>
  );
};

export default FaceGalleriesPage;
