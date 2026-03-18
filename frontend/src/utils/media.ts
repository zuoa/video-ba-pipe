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

function withApiBase(path: string): string {
  const base = getApiBase();
  if (!path) return base;
  if (!base) return path;
  return path.startsWith('/') ? `${base}${path}` : `${base}/${path}`;
}

export function buildAlertVideoUrl(relativePath?: string | null): string {
  if (!relativePath) return '';

  const trimmed = relativePath.trim();
  if (!trimmed) return '';

  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }

  if (trimmed.startsWith('/api/video/')) {
    return withApiBase(trimmed);
  }

  let normalized = trimmed.replace(/^\/+/, '');
  if (normalized.startsWith('api/video/')) {
    return withApiBase(`/${normalized}`);
  }

  if (normalized.startsWith('videos/')) {
    normalized = normalized.slice('videos/'.length);
  }

  const encodedPath = normalized
    .split('/')
    .filter(Boolean)
    .map(segment => encodeURIComponent(segment))
    .join('/');

  return withApiBase(`/api/video/videos/${encodedPath}`);
}
