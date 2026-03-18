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

function uniqueUrls(urls: string[]): string[] {
  return Array.from(new Set(urls.filter(Boolean)));
}

function normalizeVideoPath(input: string): string {
  return input.replace(/\\/g, '/').trim();
}

function extractRelativeVideoPath(input: string): string {
  let normalized = normalizeVideoPath(input).replace(/^\/+/, '');

  if (normalized.startsWith('api/video/')) {
    normalized = normalized.slice('api/video/'.length);
  }

  while (normalized.startsWith('videos/videos/')) {
    normalized = normalized.slice('videos/'.length);
  }

  const videosMarker = '/videos/';
  const videosIndex = normalized.lastIndexOf(videosMarker);
  if (videosIndex !== -1) {
    normalized = normalized.slice(videosIndex + videosMarker.length);
  }

  if (normalized.startsWith('videos/')) {
    normalized = normalized.slice('videos/'.length);
  }

  return normalized;
}

function encodeVideoPath(path: string): string {
  return path
    .split('/')
    .filter(Boolean)
    .map(segment => encodeURIComponent(segment))
    .join('/');
}

export function buildAlertVideoUrls(relativePath?: string | null): string[] {
  if (!relativePath) return [];

  const trimmed = relativePath.trim();
  if (!trimmed) return [];

  if (/^https?:\/\//i.test(trimmed)) {
    return [trimmed];
  }

  const normalized = normalizeVideoPath(trimmed);

  if (normalized.startsWith('/api/video/')) {
    const path = normalized.startsWith('/') ? normalized : `/${normalized}`;
    const relative = extractRelativeVideoPath(normalized);
    const encodedPath = relative ? encodeVideoPath(relative) : '';
    return uniqueUrls([
      withApiBase(path),
      encodedPath ? withApiBase(`/api/video/${encodedPath}`) : '',
    ]);
  }

  if (normalized.startsWith('api/video/')) {
    const relative = extractRelativeVideoPath(normalized);
    const encodedPath = relative ? encodeVideoPath(relative) : '';
    return uniqueUrls([
      withApiBase(`/${normalized}`),
      encodedPath ? withApiBase(`/api/video/${encodedPath}`) : '',
    ]);
  }

  const relative = extractRelativeVideoPath(normalized);
  if (!relative) return [];

  const encodedPath = encodeVideoPath(relative);

  return uniqueUrls([
    withApiBase(`/api/video/videos/${encodedPath}`),
    withApiBase(`/api/video/${encodedPath}`),
  ]);
}

export function buildAlertVideoUrl(relativePath?: string | null): string {
  return buildAlertVideoUrls(relativePath)[0] || '';
}
