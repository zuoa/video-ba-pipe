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

export async function uploadModel(file: File) {
  const formData = new FormData();
  formData.append('model_file', file);

  return request('/api/upload/model', {
    method: 'POST',
    data: formData,
  });
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

export async function getDetectorTemplates() {
  return request('/api/detector-templates');
}

