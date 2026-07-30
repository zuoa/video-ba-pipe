import type { ReactNode } from 'react';
import { history } from '@umijs/max';
import { App as AntdApp, ConfigProvider, message } from 'antd';
import { appTheme } from '@/theme';

const ADMIN_ONLY_PATHS = ['/users', '/system-settings', '/models', '/scripts'];

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
      localStorage.removeItem('token');
      localStorage.removeItem('user');
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
    history.push('/login');
  } else if (token && isLoginPage) {
    history.push('/dashboard');
  } else if (token && isAdminOnlyPath(location.pathname) && user?.role !== 'admin') {
    message.error('无权限访问该页面');
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
    async (response: Response) => {
      if (response.status === 401) {
        message.error('登录已过期，请重新登录');
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        history.push('/login');
      }
      return response;
    },
  ],
};
