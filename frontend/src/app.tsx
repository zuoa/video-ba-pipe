import type { ReactNode } from 'react';
import { formatMessage, history } from '@umijs/max';
import { App as AntdApp, ConfigProvider, message } from 'antd';
import { appTheme } from '@/theme';
import { getPreferredLanguage } from '@/i18n/locale';
import {
  buildLoginPath,
  clearAuthStorage,
  getLocationPath,
  handleUnauthorizedSession,
  isLoginRequestUrl,
  resolvePostLoginPath,
} from '@/utils/auth';

const ADMIN_ONLY_PATHS = ['/users', '/system-settings', '/models', '/scripts', '/face-galleries', '/reid-models'];

// Umi matches locale files by exact tag. Normalize regional variants so en-GB,
// zh-TW, and plain en/zh follow the user's language preference too.
export const locale = {
  getLocale: getPreferredLanguage,
};

function getStoredUser() {
  const userStr = localStorage.getItem('user');
  if (!userStr) {
    return null;
  }

  try {
    return JSON.parse(userStr);
  } catch (error) {
    localStorage.removeItem('user');
    return null;
  }
}

function isAdminOnlyPath(pathname: string) {
  return ADMIN_ONLY_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));
}

export async function getInitialState() {
  const token = localStorage.getItem('token');
  const storedUser = getStoredUser();
  
  if (!token || !storedUser) {
    return { currentUser: null };
  }

  try {
    const response = await fetch('/api/auth/current', {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await response.json();
    
    if (data.success) {
      localStorage.setItem('user', JSON.stringify(data.user));
      return { currentUser: data.user };
    } else {
      clearAuthStorage();
      return { currentUser: null };
    }
  } catch (error) {
    return { currentUser: null };
  }
}

export function rootContainer(container: ReactNode) {
  return (
    <ConfigProvider theme={appTheme}>
      <AntdApp>{container}</AntdApp>
    </ConfigProvider>
  );
}

export function onRouteChange({ location }: any) {
  const token = localStorage.getItem('token');
  const user = getStoredUser();
  const isLoginPage = location.pathname === '/login';
  
  if (!token && !isLoginPage) {
    history.replace(buildLoginPath(getLocationPath(location)));
  } else if (token && isLoginPage) {
    history.replace(resolvePostLoginPath(location.search));
  } else if (token && isAdminOnlyPath(location.pathname) && user?.role !== 'admin') {
    message.error(formatMessage({ id: 'app.accessDenied' }));
    history.push('/dashboard');
  }
}

export const request = {
  requestInterceptors: [
    (url: string, options: any) => {
      const token = localStorage.getItem('token');
      if (token) {
        return {
          url,
          options: {
            ...options,
            headers: {
              ...options.headers,
              Authorization: `Bearer ${token}`,
            },
          },
        };
      }
      return { url, options };
    },
  ],
  responseInterceptors: [
    [
      (response: any) => response,
      (error: any) => {
        if (error?.response?.status === 401 && !isLoginRequestUrl(error?.config?.url)) {
          if (handleUnauthorizedSession()) {
            message.error(formatMessage({ id: 'app.sessionExpired' }));
          }
        }
        return Promise.reject(error);
      },
    ],
  ],
};
