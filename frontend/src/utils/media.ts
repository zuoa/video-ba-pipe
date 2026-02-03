function getApiBase(): string {
  const envBase = (process.env.UMI_APP_API_BASE || '').trim();
  if (envBase) {
    return envBase.replace(/\/$/, '');
  }

  if (process.env.NODE_ENV === 'development') {
    return `${window.location.protocol}//${window.location.hostname}:5002`;
  }

  return '';
}

export function buildAlertVideoUrl(relativePath?: string | null): string {
  if (!relativePath) return '';
  const base = getApiBase();
  return `${base}/api/video/videos/${relativePath}`;
}
