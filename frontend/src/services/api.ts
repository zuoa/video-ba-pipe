import { request } from '@umijs/max';

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
export async function getWorkflows() {
  return request('/api/workflows');
}

export async function getWorkflow(id: number) {
  return request(`/api/workflows/${id}`);
}

export async function createWorkflow(data: any) {
  return request('/api/workflows', {
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

export async function batchCopyWorkflow(workflowId: number, sourceIds: number[]) {
  return request(`/api/workflows/${workflowId}/batch-copy`, {
    method: 'POST',
    data: { source_ids: sourceIds },
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

export async function testAlgorithm(algorithmId: number, file: File) {
  const formData = new FormData();
  formData.append('algorithm_id', algorithmId.toString());
  formData.append('image', file);

  return request('/api/algorithms/test', {
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
  return request('/api/video-sources');
}

export async function getVideoSource(id: number) {
  return request(`/api/video-sources/${id}`);
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

export async function getAlertTrend(days: number = 7) {
  return request(`/api/alerts/trend?days=${days}`);
}

// 模型
export async function getModels(params?: any) {
  return request('/api/models', { params });
}

export async function getModel(id: number) {
  return request(`/api/models/${id}`);
}

export async function getModelTypes() {
  return request('/api/models/types');
}

export async function getModelFrameworks() {
  return request('/api/models/frameworks');
}

export async function createModel(data: any) {
  return request('/api/models', {
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
  framework: string;
  version?: string;
  input_shape?: string;
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
    formData.append('framework', metadata.framework);
    if (metadata.version) formData.append('version', metadata.version);
    if (metadata.input_shape) formData.append('input_shape', metadata.input_shape);
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

// 脚本
export async function getScripts() {
  return request('/api/scripts');
}

export async function getScript(scriptPath: string) {
  return request(`/api/scripts/${encodeURIComponent(scriptPath)}`);
}

export async function createScript(data: any) {
  return request('/api/scripts', {
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

