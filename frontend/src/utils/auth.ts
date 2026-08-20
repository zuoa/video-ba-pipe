import { history } from '@umijs/max';

export const LOGIN_PATH = '/login';
export const DEFAULT_AFTER_LOGIN_PATH = '/dashboard';

const TOKEN_KEY = 'token';
const USER_KEY = 'user';

let sessionExpiredHandled = false;

export function clearAuthStorage() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function getSafeRedirect(raw: unknown): string | null {
  if (typeof raw !== 'string') {
    return null;
  }

  const value = raw.trim();
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('://')) {
    return null;
  }

  const path = value.split(/[?#]/, 1)[0];
  if (path === LOGIN_PATH || path.startsWith(`${LOGIN_PATH}/`)) {
    return null;
  }

  return value;
}

export function buildLoginPath(redirect?: string | null): string {
  const safe = getSafeRedirect(redirect);
  if (!safe) {
    return LOGIN_PATH;
  }
  return `${LOGIN_PATH}?redirect=${encodeURIComponent(safe)}`;
}

export function getLocationPath(location?: {
  pathname?: string;
  search?: string;
  hash?: string;
}): string {
  const current = location || history.location;
  return `${current.pathname || ''}${current.search || ''}${current.hash || ''}`;
}

export function resolvePostLoginPath(search?: string): string {
  const query = new URLSearchParams(search ?? history.location.search);
  return getSafeRedirect(query.get('redirect')) || DEFAULT_AFTER_LOGIN_PATH;
}

export function isLoginRequestUrl(url?: string | null): boolean {
  if (!url) {
    return false;
  }

  try {
    const path = url.startsWith('http://') || url.startsWith('https://')
      ? new URL(url).pathname
      : url.split('?')[0];
    return path === '/api/auth/login' || path.endsWith('/api/auth/login');
  } catch {
    return url.includes('/api/auth/login');
  }
}

export function isUnauthorizedError(error: unknown): boolean {
  const status = (error as { response?: { status?: number }; status?: number })?.response?.status
    ?? (error as { status?: number })?.status;
  return status === 401;
}

export function resetSessionExpiredGuard() {
  sessionExpiredHandled = false;
}

export function handleUnauthorizedSession(options?: {
  redirect?: string | null;
}): boolean {
  if (sessionExpiredHandled) {
    return false;
  }
  sessionExpiredHandled = true;

  clearAuthStorage();

  if (history.location.pathname !== LOGIN_PATH) {
    history.replace(buildLoginPath(options?.redirect ?? getLocationPath()));
  }

  window.setTimeout(resetSessionExpiredGuard, 1500);
  return true;
}
