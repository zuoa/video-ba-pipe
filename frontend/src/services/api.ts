import { request } from '@umijs/max';

function getAuthHeaders(extraHeaders?: Record<string, string>) {
  const token = localStorage.getItem('token');
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extraHeaders,
  };
}

// 认证
export async function login(data: { username: string; password: string }) {
  return request('/api/auth/login', {
    method: 'POST',
    data,
  });
}

export async function getCurrentUser() {
  return request('/api/auth/current');
}

export interface SystemInfo {
  success: boolean;
  version: string;
  company_name: string;
  node_id: string;
  node_id_source: string;
  device_model_code?: string;
  platform?: string;
  machine?: string;
  device_model?: string;
  hostname: string;
}

export async function getSystemInfo() {
  return request<SystemInfo>('/api/system/info');
}

export async function getSystemMetrics() {
  return request('/api/system/metrics');
}

export interface FaceModelArtifact {
  id: number;
  role: 'detection' | 'embedding';
  runtime: 'onnxruntime' | 'tensorrt' | 'rknn' | 'torchscript';
  architecture: string;
  device: string;
  filename: string;
  file_size: number;
  artifact_sha256: string;
  metadata: Record<string, any>;
  enabled: boolean;
}

export interface FaceModelBundle {
  id: number;
  name: string;
  version: string;
  contract_id: string;
  embedding_dimension: number;
  input_size: string;
  license_name?: string | null;
  license_url?: string | null;
  commercial_use_allowed: boolean;
  enabled: boolean;
  artifacts: FaceModelArtifact[];
}

export interface FaceGallery {
  id: number;
  name: string;
  description?: string | null;
  model_bundle_id?: number | null;
  model_bundle_name?: string | null;
  model_contract?: string | null;
  gallery_version: number;
  high_threshold: number;
  low_threshold: number;
  enabled: boolean;
  person_count: number;
  template_count: number;
  updated_at: string;
}

export interface FaceTemplate {
  id: number;
  image_mime: string;
  image_sha256: string;
  quality_score?: number | null;
  model_contract?: string | null;
  inference_backend?: string | null;
  created_at: string;
}

export interface FacePerson {
  id: number;
  person_code: string;
  name: string;
  metadata: Record<string, any>;
  enabled: boolean;
  gallery_ids: number[];
  template_count: number;
  ready_template_count: number;
  templates: FaceTemplate[];
  updated_at: string;
}

export interface FaceRuntimeStatus {
  success: boolean;
  encryption_ready: boolean;
  capabilities: {
    machine: string;
    compatible: string;
    is_rockchip: boolean;
    is_jetson: boolean;
    onnx_providers: string[];
    rknn_available: boolean;
    torch_cuda_available: boolean;
    preferred_backend?: string | null;
    supported_runtimes?: string[];
    available_runtimes?: string[];
    torch_available?: boolean;
    plugin_errors?: string[];
  };
  bundles: Array<{
    bundle_id: number;
    bundle_name: string;
    ready: boolean;
    backend?: string;
    artifacts?: Record<string, string>;
    error?: string;
  }>;
}

export async function getFaceRuntime() {
  return request<FaceRuntimeStatus>('/api/face/runtime');
}

export async function generateFaceEncryptionKey() {
  return request<{
    success: boolean;
    encryption_ready: boolean;
    created: boolean;
    source?: 'configured_file' | 'environment' | 'managed_file';
  }>('/api/face/encryption-key/generate', {
    method: 'POST',
  });
}

export async function getFaceModelBundles() {
  return request<{ success: boolean; bundles: FaceModelBundle[] }>('/api/face/model-bundles');
}

export async function createFaceModelBundle(data: Record<string, any>) {
  return request<{ success: boolean; bundle: FaceModelBundle }>('/api/face/model-bundles', {
    method: 'POST',
    data,
  });
}

export async function uploadFaceModelArtifact(bundleId: number, data: FormData) {
  return request<{ success: boolean; bundle: FaceModelBundle }>(
    `/api/face/model-bundles/${bundleId}/artifacts`,
    { method: 'POST', data },
  );
}

export async function uploadFaceModelPackage(bundleId: number, data: FormData) {
  return request<{
    success: boolean;
    profile: string;
    imported: { detection: string; embedding: string };
    bundle: FaceModelBundle;
  }>(`/api/face/model-bundles/${bundleId}/packages`, {
    method: 'POST',
    data,
  });
}

export async function getFaceGalleries() {
  return request<{ success: boolean; galleries: FaceGallery[] }>('/api/face/galleries');
}

export async function createFaceGallery(data: Record<string, any>) {
  return request<{ success: boolean; gallery: FaceGallery }>('/api/face/galleries', {
    method: 'POST',
    data,
  });
}

export async function updateFaceGallery(id: number, data: Record<string, any>) {
  return request<{ success: boolean; gallery: FaceGallery }>(`/api/face/galleries/${id}`, {
    method: 'PATCH',
    data,
  });
}

export async function deleteFaceGallery(id: number) {
  return request<{ success: boolean; deleted: boolean }>(`/api/face/galleries/${id}`, {
    method: 'DELETE',
  });
}

export async function getFacePersons(
  galleryId?: number,
  search?: string,
  page = 1,
  pageSize = 12,
) {
  return request<{
    success: boolean;
    persons: FacePerson[];
    pagination: { page: number; page_size: number; total: number; total_pages: number };
  }>('/api/face/persons', {
    params: {
      gallery_id: galleryId,
      q: search || undefined,
      page,
      page_size: pageSize,
    },
  });
}

export async function createFacePerson(data: Record<string, any>) {
  return request<{ success: boolean; person: FacePerson }>('/api/face/persons', {
    method: 'POST',
    data,
  });
}

export async function updateFacePerson(id: number, data: Record<string, any>) {
  return request<{ success: boolean; person: FacePerson }>(`/api/face/persons/${id}`, {
    method: 'PATCH',
    data,
  });
}

export async function deleteFacePerson(id: number) {
  return request<{ success: boolean; deleted: boolean }>(`/api/face/persons/${id}`, {
    method: 'DELETE',
  });
}

export async function uploadFaceTemplate(personId: number, file: File, galleryId?: number) {
  const data = new FormData();
  data.append('file', file);
  if (galleryId) data.append('gallery_id', String(galleryId));
  return request<{ success: boolean; person: FacePerson; template_id: number }>(
    `/api/face/persons/${personId}/templates`,
    { method: 'POST', data },
  );
}

export async function deleteFaceTemplate(id: number) {
  return request<{ success: boolean; deleted: boolean }>(`/api/face/templates/${id}`, {
    method: 'DELETE',
  });
}

export interface FaceImportPreflight {
  success: boolean;
  person_count: number;
  image_count: number;
  errors: Array<{ row?: number; error: string }>;
}

export interface FaceImportJob {
  id: number;
  gallery_id: number;
  status: 'pending' | 'processing' | 'completed' | 'completed_with_errors' | 'failed';
  total_people: number;
  total_images: number;
  processed_people: number;
  succeeded_people: number;
  failed_people: number;
  errors: Array<{ row?: number; person_code?: string; error?: string; warning?: string }>;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

export async function preflightFaceImport(file: File) {
  const data = new FormData();
  data.append('file', file);
  return request<FaceImportPreflight>('/api/face/imports/preflight', {
    method: 'POST',
    data,
  });
}

export async function createFaceImport(galleryId: number, file: File) {
  const data = new FormData();
  data.append('gallery_id', String(galleryId));
  data.append('file', file);
  return request<{ success: boolean; job: FaceImportJob }>('/api/face/imports', {
    method: 'POST',
    data,
  });
}

export async function getFaceImport(id: number) {
  return request<{ success: boolean; job: FaceImportJob }>(`/api/face/imports/${id}`);
}

export interface FaceCalibrationResult {
  success: boolean;
  gallery_id: number;
  suggested_low_threshold: number;
  suggested_high_threshold: number;
  target_fpir: number;
  measured_fpir: number;
  measured_fnir?: number | null;
  genuine_pair_count: number;
  impostor_pair_count: number;
  template_count: number;
  sampled: boolean;
  applied: false;
}

export async function calibrateFaceThresholds(galleryId: number, targetFpir = 0.001) {
  return request<FaceCalibrationResult>('/api/face/calibrations', {
    method: 'POST',
    data: { gallery_id: galleryId, target_fpir: targetFpir },
  });
}

export interface FaceRecognitionEvent {
  id: number;
  gallery_id?: number | null;
  person_id?: number | null;
  person_code?: string | null;
  person_name?: string | null;
  track_id?: string | null;
  identity_status: 'known' | 'unknown';
  similarity?: number | null;
  threshold?: number | null;
  quality: Record<string, any>;
  snapshot_path?: string | null;
  liveness_status: 'not_checked';
  model_contract?: string | null;
  inference_backend?: string | null;
  occurred_at: string;
  expires_at?: string | null;
}

export async function getFaceEvents(galleryId?: number, identityStatus?: string) {
  return request<{ success: boolean; events: FaceRecognitionEvent[] }>('/api/face/events', {
    params: {
      gallery_id: galleryId,
      identity_status: identityStatus || undefined,
      limit: 100,
    },
  });
}

export async function getFaceEventSnapshot(id: number) {
  return request<Blob>(`/api/face/events/${id}/snapshot`, {
    responseType: 'blob',
  });
}

export interface LicenseStatus {
  success: boolean;
  tier: 'free' | 'licensed';
  license_status: string;
  message: string;
  limits: { video_sources: number; algorithms: number };
  usage: { video_sources: number; algorithms: number };
  over_limit: { video_sources: boolean; algorithms: boolean };
  expires_at?: string | null;
  license_id?: string | null;
  customer?: string | null;
  node_id?: string;
  licensed_node_id?: string | null;
  entitled_source_ids?: number[] | null;
  entitled_algorithm_ids?: number[] | null;
}

export async function getLicenseStatus() {
  return request<LicenseStatus>('/api/license/status');
}

export async function installLicense(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  return request<LicenseStatus & { message: string }>('/api/license/install', {
    method: 'POST',
    data: formData,
  });
}

export interface ManagedApiKey {
  id: number;
  name: string;
  key_prefix: string;
  enabled: boolean;
  created_at: string;
  last_used_at?: string | null;
  created_by: string;
  key?: string;
}

export async function getApiKeys() {
  return request<{ success: boolean; keys: ManagedApiKey[] }>('/api/system/api-keys');
}

export async function createApiKey(name: string) {
  return request<{ success: boolean; key: ManagedApiKey; message: string }>('/api/system/api-keys', {
    method: 'POST',
    data: { name },
  });
}

export async function setApiKeyEnabled(id: number, enabled: boolean) {
  return request<{ success: boolean; key: ManagedApiKey }>(`/api/system/api-keys/${id}`, {
    method: 'PATCH',
    data: { enabled },
  });
}

export async function downloadOpenApiSpec() {
  return request<Blob>('/api/system/openapi/spec', {
    responseType: 'blob',
  });
}

export async function downloadOpenApiGuide() {
  return request<Blob>('/api/system/openapi/guide', {
    responseType: 'blob',
  });
}

export async function getVlConfig() {
  return request('/api/system/vl-config');
}

export async function updateVlConfig(data: any) {
  return request('/api/system/vl-config', {
    method: 'PUT',
    data,
  });
}

export async function getSourceRotationConfig() {
  return request('/api/system/source-rotation-config');
}

export async function updateSourceRotationConfig(data: any) {
  return request('/api/system/source-rotation-config', {
    method: 'PUT',
    data,
  });
}

export interface InferenceResourceConfig {
  shared_inference_enabled: boolean;
  gpu_scheduling_enabled: boolean;
  gpu_scheduling_policy: 'balanced';
  gpu_allowed_devices: string[];
  gpu_memory_reserve_mb: number;
  gpu_new_model_default_mb: number;
  gpu_model_memory_margin_percent: number;
  gpu_oom_cooldown_seconds: number;
  gpu_nvml_stale_seconds: number;
  gpu_failure_mode: 'reject' | 'legacy';
  inference_admission_enabled: boolean;
  system_reserve_mb: number;
  system_reserve_percent: number;
  new_model_default_mb: number;
  model_memory_margin_percent: number;
  queue_size: number;
  batch_max_size: number;
  batch_wait_ms: number;
  request_timeout_seconds: number;
  model_idle_seconds: number;
  oom_circuit_breaker_enabled: boolean;
  oom_failure_threshold: number;
  oom_circuit_open_seconds: number;
  oom_stable_reset_seconds: number;
  oom_restart_backoff_max_seconds: number;
}

export interface InferenceModelStatus {
  model_id?: number | string | null;
  pid?: number | null;
  alive?: boolean;
  ready?: boolean;
  pss_mb?: number | null;
  rss_mb?: number | null;
  references?: number;
  queue_depth?: number | null;
  oom_failures?: number;
  gpu_index?: number | null;
  gpu_uuid?: string | null;
  gpu_name?: string | null;
  reserved_gpu_mb?: number | null;
  actual_gpu_mb?: number | null;
  gpu_retry_count?: number;
}

export interface InferenceGpuStatus {
  index: number;
  uuid: string;
  name: string;
  total_mb: number;
  used_mb: number;
  free_mb: number;
  utilization_percent?: number | null;
  pending_reserved_mb: number;
  assignment_count: number;
  cooldown_seconds: number;
}

export interface InferenceResourceStatus {
  worker_online: boolean;
  status_age_seconds?: number | null;
  platform?: string;
  capabilities?: Record<string, any>;
  config_source?: string;
  applied_config_revision?: string;
  effective_config?: InferenceResourceConfig;
  service_running?: boolean;
  service_pid?: number | null;
  model_count?: number;
  models?: InferenceModelStatus[];
  gpu_scheduler?: {
    enabled?: boolean;
    failure_mode?: 'reject' | 'legacy';
    degraded_to_legacy?: boolean;
    metrics_stale?: boolean;
    metrics_error?: string | null;
    reserve_mb?: number;
    allowed_devices?: string[];
  };
  gpus?: InferenceGpuStatus[];
  source_host_count?: number;
  memory?: {
    total_mb: number;
    available_mb: number;
    used_mb: number;
    usage_percent: number;
    swap_total_mb: number;
    swap_used_mb: number;
    swap_usage_percent: number;
  };
  reconcile_error?: string | null;
}

export interface InferenceResourceResponse {
  success: boolean;
  config: InferenceResourceConfig;
  configured_revision: string;
  config_source: string;
  effective_config: InferenceResourceConfig;
  capabilities: Record<string, any>;
  status: InferenceResourceStatus;
  config_pending: boolean;
  restart_required: boolean;
  apply_mode: 'worker_auto_reconcile';
  message?: string;
}

export async function getInferenceResourceConfig() {
  return request<InferenceResourceResponse>('/api/system/inference-resource-config');
}

export async function updateInferenceResourceConfig(data: InferenceResourceConfig) {
  return request<InferenceResourceResponse>('/api/system/inference-resource-config', {
    method: 'PUT',
    data,
  });
}

export interface VideoDecodeConfig {
  decode_keyframes_only: boolean;
}

export interface VideoDecodeConfigResponse {
  success: boolean;
  config: VideoDecodeConfig;
  config_source: 'database' | 'environment';
  apply_mode: 'worker_auto_restart';
  message?: string;
}

export async function getVideoDecodeConfig() {
  return request<VideoDecodeConfigResponse>('/api/system/video-decode-config');
}

export async function updateVideoDecodeConfig(data: VideoDecodeConfig) {
  return request<VideoDecodeConfigResponse>('/api/system/video-decode-config', {
    method: 'PUT',
    data,
  });
}

export interface FaceRecognitionConfig {
  known_retention_days: number;
  unknown_retention_days: number;
  inference_backend: string;
  require_commercial_models: boolean;
}

export interface FaceRecognitionConfigResponse {
  success: boolean;
  config: FaceRecognitionConfig;
  config_source: 'database' | 'default' | 'cache';
  available_backends: string[];
  apply_mode: 'dynamic_on_next_face_runtime';
  message?: string;
}

export async function getFaceRecognitionConfig() {
  return request<FaceRecognitionConfigResponse>('/api/system/face-recognition-config');
}

export async function updateFaceRecognitionConfig(data: FaceRecognitionConfig) {
  return request<FaceRecognitionConfigResponse>('/api/system/face-recognition-config', {
    method: 'PUT',
    data,
  });
}

export interface RecordingStorageConfig {
  recording_enabled: boolean;
  pre_alert_seconds: number;
  post_alert_seconds: number;
  recording_fps: number;
  video_max_gb: number;
  image_max_gb: number;
  min_free_gb: number;
  stop_recording_percent: number;
  metadata_only_percent: number;
}

export interface RecordingStorageUsage {
  video_bytes: number;
  image_bytes: number;
  disk_total_bytes: number;
  disk_used_bytes: number;
  disk_free_bytes: number;
  disk_used_percent: number;
  pressure_level: 'normal' | 'recording_stopped' | 'metadata_only';
}

export async function getRecordingStorageConfig() {
  return request<{
    success: boolean;
    config: RecordingStorageConfig;
    usage: RecordingStorageUsage;
  }>('/api/system/recording-storage-config');
}

export async function updateRecordingStorageConfig(data: RecordingStorageConfig) {
  return request('/api/system/recording-storage-config', {
    method: 'PUT',
    data,
  });
}

export interface OpsNotificationConfig {
  enabled: boolean;
  webhook_url: string;
  secret: string;
  secret_configured?: boolean;
  notify_disk_pressure: boolean;
  notify_cleanup_failure: boolean;
  notify_alert_growth: boolean;
  alert_growth_window_minutes: number;
  alert_growth_threshold: number;
  cooldown_minutes: number;
}

export async function getOpsNotificationConfig() {
  return request<{ success: boolean; config: OpsNotificationConfig }>(
    '/api/system/ops-notification-config',
  );
}

export async function updateOpsNotificationConfig(data: OpsNotificationConfig) {
  return request('/api/system/ops-notification-config', {
    method: 'PUT',
    data,
  });
}

export async function testOpsNotificationConfig(data: OpsNotificationConfig) {
  return request('/api/system/ops-notification-config/test', {
    method: 'POST',
    data,
  });
}

export interface PublicMediaConfig {
  public_base_url: string;
  public_base_url_override?: string;
  sign_media_urls: boolean;
  media_url_ttl_hours: number;
  delivery_mode: 'url' | 'inline' | 'object_storage';
  inline: {
    max_bytes: number;
    max_edge: number;
    jpeg_quality: number;
  };
  object_storage: {
    endpoint_url: string;
    region: string;
    bucket: string;
    access_key_id: string;
    secret_access_key: string;
    secret_configured?: boolean;
    key_prefix: string;
    force_path_style: boolean;
    verify_ssl: boolean;
    presigned_url_ttl_hours: number;
  };
  async_delivery: {
    max_attempts: number;
    initial_backoff_seconds: number;
    max_backoff_seconds: number;
  };
  signing_available?: boolean;
  config_source?: string;
}

export interface AlertDeliveryStats {
  pending: number;
  processing: number;
  retrying: number;
  failed: number;
}

export async function getPublicMediaConfig() {
  return request<{ success: boolean; config: PublicMediaConfig; delivery_stats: AlertDeliveryStats }>(
    '/api/system/public-media-config',
  );
}

export async function updatePublicMediaConfig(data: PublicMediaConfig) {
  return request('/api/system/public-media-config', {
    method: 'PUT',
    data,
  });
}

export async function testObjectStorageConfig(data: PublicMediaConfig) {
  return request<{ success: boolean; message?: string; error?: string }>(
    '/api/system/public-media-config/test-object-storage',
    { method: 'POST', data },
  );
}

export async function retryFailedAlertDeliveries() {
  return request<{ success: boolean; retried: number; delivery_stats: AlertDeliveryStats; message: string }>(
    '/api/system/alert-deliveries/retry-failed',
    { method: 'POST' },
  );
}

export interface RabbitMqConfig {
  enabled: boolean;
  host: string;
  port: number;
  username: string;
  password: string;
  password_configured?: boolean;
  vhost: string;
  alert_queue: string;
  alert_exchange: string;
  alert_routing_key: string;
  exchange_type: 'topic' | 'direct';
  connection_timeout_seconds: number;
}

export interface MqttConfig {
  host: string;
  port: number;
  username: string;
  password: string;
  password_configured?: boolean;
  topic_prefix: string;
  connection_timeout_seconds: number;
  publish_timeout_seconds: number;
  keepalive_seconds: number;
}

export interface HttpCustomHeader {
  name: string;
  value: string;
  value_configured?: boolean;
}

export interface HttpDeliveryConfig {
  endpoint_url: string;
  hmac_secret: string;
  hmac_secret_configured?: boolean;
  custom_headers: HttpCustomHeader[];
  timeout_seconds: number;
}

export interface MessageQueueConfig {
  enabled: boolean;
  provider: 'mqtt' | 'rabbitmq' | 'http';
  mqtt: MqttConfig;
  rabbitmq: RabbitMqConfig;
  http: HttpDeliveryConfig;
}

export async function getMessageQueueConfig() {
  return request<{ success: boolean; config: MessageQueueConfig }>(
    '/api/system/message-queue-config',
  );
}

export async function updateMessageQueueConfig(data: MessageQueueConfig) {
  return request<{ success: boolean; config: MessageQueueConfig; message?: string }>(
    '/api/system/message-queue-config',
    { method: 'PUT', data },
  );
}

export async function testMessageQueueConfig(data: MessageQueueConfig) {
  return request<{ success: boolean; message?: string; error?: string }>(
    '/api/system/message-queue-config/test',
    { method: 'POST', data },
  );
}

export async function getRabbitMqConfig() {
  return request<{ success: boolean; config: RabbitMqConfig }>(
    '/api/system/rabbitmq-config',
  );
}

export async function updateRabbitMqConfig(data: Partial<RabbitMqConfig>) {
  return request('/api/system/rabbitmq-config', {
    method: 'PUT',
    data,
  });
}

export async function testRabbitMqConfig(data: Partial<RabbitMqConfig>) {
  return request('/api/system/rabbitmq-config/test', {
    method: 'POST',
    data,
  });
}

export async function getUsers() {
  return request('/api/auth/users');
}

export async function createUser(data: any) {
  return request('/api/auth/users', {
    method: 'POST',
    data,
  });
}

export async function updateUser(id: number, data: any) {
  return request(`/api/auth/users/${id}`, {
    method: 'PUT',
    data,
  });
}

export async function deleteUser(id: number) {
  return request(`/api/auth/users/${id}`, {
    method: 'DELETE',
  });
}

// 工作流
export interface Workflow {
  id: number;
  name: string;
  description?: string | null;
  workflow_data: {
    nodes?: any[];
    connections?: any[];
  };
  is_active: boolean;
  is_template: boolean;
  source_template_id?: number | null;
  source_template_name?: string | null;
  video_source_id?: number | null;
  config_version: number;
  created_at?: string | null;
  updated_at?: string | null;
  created_by?: string;
}

export interface WorkflowFormValues {
  name: string;
  description?: string;
  is_template: boolean;
}

export interface VideoSource {
  id: number;
  name: string;
  source_code: string;
  status: string;
  enabled?: boolean;
  source_codec?: string;
  source_url?: string;
  source_decode_width?: number;
  source_decode_height?: number;
  source_fps?: number;
  decode_keyframes_only?: boolean | null;
}

export interface BatchCopyWorkflowResponse {
  success: boolean;
  template: { id: number; name: string };
  created: Array<{
    workflow_id: number;
    source_id: number;
    name: string;
    source_name: string;
    is_active: boolean;
    source_template_id: number;
  }>;
  errors: Array<{
    source_id: number;
    code?: string;
    error: string;
    existing_workflow_id?: number | null;
    existing_workflow_name?: string | null;
  }>;
  summary: { total: number; success: number; failed: number };
  error?: string;
}

export async function getWorkflows() {
  return request<Workflow[]>('/api/workflows');
}

export async function getWorkflow(id: number) {
  return request<Workflow>(`/api/workflows/${id}`);
}

export async function createWorkflow(data: WorkflowFormValues) {
  return request<{ id: number; message: string }>('/api/workflows', {
    method: 'POST',
    data,
  });
}

export async function updateWorkflow(id: number, data: any) {
  return request(`/api/workflows/${id}`, {
    method: 'PUT',
    data,
  });
}

export async function deleteWorkflow(id: number) {
  return request(`/api/workflows/${id}`, {
    method: 'DELETE',
  });
}

export async function activateWorkflow(id: number) {
  return request(`/api/workflows/${id}/activate`, {
    method: 'POST',
  });
}

export async function deactivateWorkflow(id: number) {
  return request(`/api/workflows/${id}/deactivate`, {
    method: 'POST',
  });
}

export async function batchCopyWorkflow(
  workflowId: number,
  sourceIds: number[],
  isActive: boolean,
) {
  return request<BatchCopyWorkflowResponse>(`/api/workflows/${workflowId}/batch-copy`, {
    method: 'POST',
    data: { source_ids: sourceIds, is_active: isActive },
  });
}

export async function batchActivateWorkflows(workflowIds: number[]) {
  return request('/api/workflows/batch-activate', {
    method: 'POST',
    data: { workflow_ids: workflowIds },
  });
}

export async function batchDeactivateWorkflows(workflowIds: number[]) {
  return request('/api/workflows/batch-deactivate', {
    method: 'POST',
    data: { workflow_ids: workflowIds },
  });
}

export async function batchDeleteWorkflows(workflowIds: number[]) {
  return request('/api/workflows/batch-delete', {
    method: 'POST',
    data: { workflow_ids: workflowIds },
  });
}

export interface WorkflowBatchConfigTarget {
  workflow_ids: number[];
  node_id: string;
  node_type: 'algorithm' | 'alert' | 'time_schedule';
  changes: Record<string, any>;
}

export interface WorkflowBatchConfigResponse {
  success: boolean;
  dry_run: boolean;
  summary: {
    workflow_count: number;
    active_count: number;
    node_change_count: number;
  };
  changes: Array<{
    workflow_id: number;
    workflow_name: string;
    node_id: string;
    node_name: string;
    node_type: string;
    fields: string[];
    is_active: boolean;
  }>;
  message?: string;
}

export async function batchConfigWorkflows(data: {
  workflow_ids: number[];
  expected_versions: Record<string, number>;
  targets: WorkflowBatchConfigTarget[];
  dry_run: boolean;
}) {
  return request<WorkflowBatchConfigResponse>('/api/workflows/batch-config', {
    method: 'POST',
    data,
  });
}

export interface TemplateTransferManifest {
  format: 'video-ba-workflow-template';
  schema_version: number;
  created_at: string;
  source: {
    device_model_code: string;
    app_version: string;
    platform: string;
    machine: string;
    device_model?: string;
  };
  template: {
    portable_id: string;
    name: string;
    description?: string | null;
    workflow_path: string;
  };
  options: { models_included: boolean };
  dependencies: {
    models: Array<Record<string, any>>;
    algorithms: Array<Record<string, any>>;
    external_apis: Array<Record<string, any>>;
    hooks?: Array<Record<string, any>>;
  };
  required_inputs: Array<{ key: string; label: string; secret?: boolean }>;
  entries: Array<{ path: string; size: number; sha256: string }>;
}

export interface TemplateImportPreflight {
  success: boolean;
  compatible: boolean;
  ready: boolean;
  source: TemplateTransferManifest['source'];
  target: TemplateTransferManifest['source'];
  template: {
    portable_id: string;
    name: string;
    status: 'import' | 'conflict' | 'already_imported';
    existing_id?: number | null;
  };
  dependencies: {
    models: Array<Record<string, any>>;
    algorithms: Array<Record<string, any>>;
    external_apis: Array<Record<string, any>>;
    hooks: Array<Record<string, any>>;
  };
  required_inputs: Array<{ key: string; label: string; secret?: boolean }>;
  missing_inputs: Array<{ key: string; label: string; secret?: boolean }>;
  blockers: Array<Record<string, any>>;
}

export type TemplateImportResolutions = {
  models?: Record<string, { target_id?: number; action?: 'rename'; name?: string; version?: string }>;
  algorithms?: Record<string, { target_id?: number; action?: 'rename'; name?: string }>;
  external_apis?: Record<string, { target_id?: number; action?: 'rename'; name?: string }>;
  hooks?: Record<string, { target_id?: number; action?: 'rename'; name?: string }>;
  template?: { action?: 'rename'; name?: string };
  secrets?: Record<string, string>;
};

export async function getTemplateTransferCapabilities() {
  return request<{
    success: boolean;
    configured: boolean;
    device_model_code: string;
    app_version: string;
    platform: string;
    machine: string;
    device_model?: string;
  }>('/api/workflow-template-transfers/capabilities');
}

export async function downloadWorkflowTemplate(id: number, includeModels: boolean) {
  const response = await fetch(`/api/workflow-templates/${id}/export`, {
    method: 'POST',
    headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ include_models: includeModels }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || '导出失败');
  }
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const matched = disposition.match(/filename\*?=(?:UTF-8''|"?)([^";]+)"?/i);
  const filename = decodeURIComponent(matched?.[1] || `workflow-template-${id}.vbt.zip`);
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}

export async function preflightWorkflowTemplate(
  manifest: TemplateTransferManifest,
  resolutions: TemplateImportResolutions = {},
) {
  return request<TemplateImportPreflight>('/api/workflow-template-imports/preflight', {
    method: 'POST',
    data: { manifest, resolutions },
  });
}

export async function importWorkflowTemplate(
  file: File,
  resolutions: TemplateImportResolutions,
) {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('resolutions', JSON.stringify(resolutions));
  const response = await fetch('/api/workflow-template-imports', {
    method: 'POST',
    headers: getAuthHeaders(),
    body: formData,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || '导入失败');
  return data;
}

// 算法
export async function getAlgorithms() {
  return request('/api/algorithms');
}

export async function getAlgorithm(id: number) {
  return request(`/api/algorithms/${id}`);
}

export async function createAlgorithm(data: any) {
  return request('/api/algorithms', {
    method: 'POST',
    data,
  });
}

export async function updateAlgorithm(id: number, data: any) {
  return request(`/api/algorithms/${id}`, {
    method: 'PUT',
    data,
  });
}

export async function deleteAlgorithm(id: number) {
  return request(`/api/algorithms/${id}`, {
    method: 'DELETE',
  });
}

export async function getPluginModules() {
  return request('/api/plugins/modules');
}

// 外部 API
export async function getExternalApis() {
  return request('/api/external-apis');
}

export async function getExternalApi(id: number) {
  return request(`/api/external-apis/${id}`);
}

export async function createExternalApi(data: any) {
  return request('/api/external-apis', {
    method: 'POST',
    data,
  });
}

export async function updateExternalApi(id: number, data: any) {
  return request(`/api/external-apis/${id}`, {
    method: 'PUT',
    data,
  });
}

export async function deleteExternalApi(id: number) {
  return request(`/api/external-apis/${id}`, {
    method: 'DELETE',
  });
}

export async function testAlgorithm(algorithmId: number, file: File) {
  const formData = new FormData();
  formData.append('algorithm_id', algorithmId.toString());
  formData.append('image', file);

  return request('/api/algorithms/test', {
    method: 'POST',
    data: formData,
  });
}

export async function previewCascadeAlgorithm(cascadeConfig: unknown, file: File) {
  const formData = new FormData();
  formData.append('cascade_config', JSON.stringify(cascadeConfig));
  formData.append('image', file);
  return request('/api/algorithms/cascade/preview', {
    method: 'POST',
    data: formData,
  });
}

export async function testAlgorithmWithBase64(algorithmId: number, base64Image: string) {
  // 将 base64 转换为 blob
  const response = await fetch(base64Image);
  const blob = await response.blob();

  const formData = new FormData();
  formData.append('algorithm_id', algorithmId.toString());
  formData.append('image', blob, 'test.jpg');

  return request('/api/algorithms/test', {
    method: 'POST',
    data: formData,
  });
}

// 视频源
export async function getVideoSources() {
  return request<VideoSource[]>('/api/video-sources');
}

export async function getVideoSource(id: number) {
  return request<VideoSource>(`/api/video-sources/${id}`);
}

export async function createVideoSource(data: any) {
  return request('/api/video-sources', {
    method: 'POST',
    data,
  });
}

export async function updateVideoSource(id: number, data: any) {
  return request(`/api/video-sources/${id}`, {
    method: 'PUT',
    data,
  });
}

export async function deleteVideoSource(id: number) {
  return request(`/api/video-sources/${id}`, {
    method: 'DELETE',
  });
}

export async function getSourceHealth(id: number) {
  return request(`/api/video-sources/${id}/health`);
}

// ===== 实时预览（WebRTC / MediaMTX + 最新检测帧）=====
export async function getPreviewConfig() {
  return request('/api/preview/config');
}

export async function ensurePreviewPath(sourceId: number) {
  return request(`/api/preview/ensure/${sourceId}`, { method: 'POST' });
}

export async function getSourceImportProviders() {
  return request('/api/source-import/providers');
}

export async function discoverImportChannels(data: any) {
  return request('/api/source-import/discover', {
    method: 'POST',
    data,
  });
}

export async function commitImportChannels(data: any) {
  return request('/api/source-import/commit', {
    method: 'POST',
    data,
  });
}

export async function scanOnvifDevices(data: any) {
  return request('/api/onvif/scan', {
    method: 'POST',
    data,
    timeout: 20000,
  });
}

export async function fetchOnvifProfiles(data: any) {
  return request('/api/onvif/profiles', {
    method: 'POST',
    data,
    timeout: 20000,
  });
}

export async function importOnvifSources(data: any) {
  return request('/api/onvif/import', {
    method: 'POST',
    data,
  });
}

export async function detectStreamInfo(url: string) {
  return request('/api/stream/detect', {
    method: 'POST',
    data: { url },
  });
}

// 视频文件管理
export async function uploadVideoFile(file: File) {
  const formData = new FormData();
  formData.append('file', file);

  return request('/api/video-sources/upload', {
    method: 'POST',
    data: formData,
    requestType: 'form',
  });
}

export async function getVideoFiles() {
  return request('/api/video-sources/files');
}

export async function deleteVideoFile(filename: string) {
  return request(`/api/video-sources/files/${filename}`, {
    method: 'DELETE',
  });
}

export async function captureFrame(videoSourceId: number) {
  return request(`/api/workflows/capture_frame/${videoSourceId}`, {
    method: 'GET',
  });
}

// 告警
export async function getAlerts(params?: any) {
  return request('/api/alerts', {
    params,
  });
}

export async function getAlertTypes() {
  return request('/api/alert-types');
}

export async function getTodayAlertsCount() {
  return request('/api/alerts/today-count');
}

export type AlertStatsPeriod = 'hour' | 'day' | 'week' | 'month' | 'year';

export async function getAlertTrend(period: AlertStatsPeriod = 'day') {
  return request(`/api/alerts/trend?period=${period}`);
}

export async function getChannelAlertStats(period: AlertStatsPeriod = 'day') {
  return request(`/api/alerts/channel-stats?period=${period}`);
}

export interface AlertExportTask {
  id: number;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | string;
  created_by: string;
  filters: Record<string, string>;
  filter_summary: string;
  total_count: number;
  processed_count: number;
  missing_image_count: number;
  progress_percent: number;
  file_name?: string | null;
  file_path?: string | null;
  file_url?: string | null;
  file_size?: number | null;
  error_message?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  expires_at?: string | null;
  downloadable: boolean;
  message?: string;
}

export interface AlertExportListResponse {
  data: AlertExportTask[];
  pagination: {
    page: number;
    per_page: number;
    total: number;
    total_pages: number;
  };
}

export async function createAlertExport(data?: Record<string, unknown>) {
  return request<AlertExportTask>('/api/alert-exports', {
    method: 'POST',
    data: data || {},
  });
}

export async function getAlertExports(params?: { page?: number; per_page?: number }) {
  return request<AlertExportListResponse>('/api/alert-exports', {
    params,
  });
}

export async function getAlertExport(id: number) {
  return request<AlertExportTask>(`/api/alert-exports/${id}`);
}

export async function cancelAlertExport(id: number) {
  return request<AlertExportTask>(`/api/alert-exports/${id}/cancel`, {
    method: 'POST',
  });
}

export async function deleteAlertExport(id: number) {
  return request<{ success: boolean }>(`/api/alert-exports/${id}`, {
    method: 'DELETE',
  });
}

export async function downloadAlertExport(id: number, fileUrl?: string | null) {
  if (fileUrl) {
    const anchor = document.createElement('a');
    anchor.href = fileUrl;
    anchor.rel = 'noopener';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    return;
  }

  const response = await fetch(`/api/alert-exports/${id}/download`, {
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || '下载失败');
  }

  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const matched = disposition.match(/filename\*?=(?:UTF-8''|"?)([^";]+)"?/i);
  const filename = matched?.[1] || `alerts-export-${id}.zip`;

  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = decodeURIComponent(filename);
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}

// 模型
export async function getModels(params?: any) {
  return request('/api/models/', { params });
}

export async function getModel(id: number) {
  return request(`/api/models/${id}`);
}

export interface ModelQuickSetupResource {
  id: number;
  name: string;
  created: boolean;
}

export interface ModelQuickSetupPreview {
  success: boolean;
  eligible: boolean;
  reason?: string | null;
  model: {
    id: number;
    name: string;
    model_type: string;
    framework: string;
  };
  script?: {
    name: string;
    path: string;
    version: string;
  } | null;
  defaults: {
    algorithm_name: string;
    template_name: string;
  };
  existing: {
    algorithm?: ModelQuickSetupResource | null;
    workflow_template?: ModelQuickSetupResource | null;
  };
}

export interface ModelQuickSetupResult {
  success: boolean;
  message: string;
  algorithm: ModelQuickSetupResource;
  workflow_template: ModelQuickSetupResource;
}

export async function getModelQuickSetup(id: number) {
  return request<ModelQuickSetupPreview>(`/api/models/${id}/quick-setup`);
}

export async function createModelQuickSetup(
  id: number,
  data: { algorithm_name: string; template_name: string },
) {
  return request<ModelQuickSetupResult>(`/api/models/${id}/quick-setup`, {
    method: 'POST',
    data,
  });
}

export async function downloadModelFile(id: number) {
  const response = await fetch(`/api/models/${id}/download`, {
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || '下载失败');
  }

  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const matched = disposition.match(/filename="?([^"]+)"?/i);
  const filename = matched?.[1] || `model-${id}`;

  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = decodeURIComponent(filename);
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}

export async function getModelTypes() {
  return request('/api/models/types');
}

export async function getModelFrameworks() {
  return request('/api/models/frameworks');
}

export async function createModel(data: any) {
  return request('/api/models/', {
    method: 'POST',
    data,
  });
}

export async function updateModel(id: number, data: any) {
  return request(`/api/models/${id}`, {
    method: 'PUT',
    data,
  });
}

export async function deleteModel(id: number) {
  return request(`/api/models/${id}`, {
    method: 'DELETE',
  });
}

export async function uploadModel(file: File, metadata?: {
  name: string;
  model_type: string;
  model_role?: string;
  framework: string;
  version?: string;
  input_shape?: string;
  model_postprocess?: string;
  description?: string;
}) {
  // 验证文件对象
  if (!file || !(file instanceof File)) {
    throw new Error('无效的文件对象');
  }

  console.log('开始上传模型文件:', {
    fileName: file.name,
    fileSize: file.size,
    fileType: file.type,
    metadata,
  });

  const formData = new FormData();

  // 添加文件
  formData.append('file', file);

  // 添加其他元数据
  if (metadata) {
    formData.append('name', metadata.name);
    formData.append('model_type', metadata.model_type);
    if (metadata.model_role) formData.append('model_role', metadata.model_role);
    formData.append('framework', metadata.framework);
    if (metadata.version) formData.append('version', metadata.version);
    if (metadata.input_shape) formData.append('input_shape', metadata.input_shape);
    if (metadata.model_postprocess) formData.append('model_postprocess', metadata.model_postprocess);
    if (metadata.description) formData.append('description', metadata.description);
  }

  // 验证 FormData
  console.log('FormData 内容检查:');
  for (const [key, value] of formData.entries()) {
    console.log(`  ${key}:`, value instanceof File ? `File(${value.name}, ${value.size} bytes)` : value);
  }

  // 使用原生 fetch API 上传文件，避免 axios 处理 FormData 的问题
  // 注意：URL 末尾需要斜杠，否则会返回 308 重定向
  const token = localStorage.getItem('token');
  const response = await fetch('/api/models/', {
    method: 'POST',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      // 不要设置 Content-Type，让浏览器自动设置 multipart/form-data 边界
    },
    body: formData,
  });

  const data = await response.json();

  if (!response.ok) {
    console.error('上传失败:', data);
    throw new Error(data.error || '上传失败');
  }

  console.log('上传成功:', data);
  return data;
}

export async function importModelFromSource(data: {
  source_type: 'url' | 'huggingface';
  name?: string;
  model_type: string;
  model_role?: string;
  framework: string;
  version?: string;
  input_shape?: string;
  model_postprocess?: string;
  description?: string;
  source_url?: string;
  repo_id?: string;
  filename?: string;
  revision?: string;
  hf_token?: string;
  use_hf_mirror?: boolean;
}) {
  return request('/api/models/import', {
    method: 'POST',
    data,
  });
}

// 脚本
export async function getScripts() {
  return request('/api/scripts/');
}

export async function getScript(scriptPath: string) {
  return request(`/api/scripts/${encodeURIComponent(scriptPath)}`);
}

export async function createScript(data: any) {
  return request('/api/scripts/upload', {
    method: 'POST',
    data,
  });
}

export async function updateScript(scriptPath: string, data: any) {
  return request(`/api/scripts/${encodeURIComponent(scriptPath)}`, {
    method: 'PUT',
    data,
  });
}

export async function getScriptConfigSchema(scriptPath: string) {
  return request(`/api/scripts/config-schema/${encodeURIComponent(scriptPath)}`);
}

export async function deleteScript(scriptPath: string) {
  return request(`/api/scripts/${encodeURIComponent(scriptPath)}`, {
    method: 'DELETE',
  });
}

export async function validateScript(data: any) {
  return request('/api/scripts/validate', {
    method: 'POST',
    data,
  });
}

export async function getScriptTemplates() {
  return request('/api/scripts/templates');
}

export async function getDetectorTemplates(params?: { is_system?: boolean }) {
  return request('/api/detector-templates', { params });
}

export async function getDetectorScriptConfig(scriptPath: string) {
  return request(`/api/detector-templates/script-config/${encodeURIComponent(scriptPath)}`);
}

export async function createAlgorithmFromWizard(data: any) {
  return request('/api/algorithms', {
    method: 'POST',
    data,
  });
}

// 类型定义
export interface DetectorTemplate {
  id: number;
  name: string;
  description: string;
  script_path: string;
  is_system: boolean;
  tags_list?: string[];
}

export interface Script {
  name: string;
  path: string;
  category?: string;
}

// 工作流测试
export async function testWorkflow(workflowId: number, imageBase64: string) {
  return request(`/api/workflows/${workflowId}/test`, {
    method: 'POST',
    data: { image: imageBase64 },
  });
}

export async function testWorkflowWithFile(workflowId: number, file: File) {
  const formData = new FormData();
  formData.append('media', file);

  return request(`/api/workflows/${workflowId}/test`, {
    method: 'POST',
    data: formData,
  });
}

export async function getWorkflowTestResults(params?: any) {
  return request('/api/workflow-test-results', {
    params,
  });
}
