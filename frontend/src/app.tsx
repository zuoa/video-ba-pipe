import type { ReactNode } from 'react';
import { history } from '@umijs/max';
import { App as AntdApp, ConfigProvider, message } from 'antd';

const appTheme = {
  token: {
    colorPrimary: '#1f242b',
    colorInfo: '#1f242b',
    colorSuccess: '#2f6b4f',
    colorWarning: '#8a5cf6',
    colorError: '#c4544f',
    colorText: '#1f2328',
    colorTextSecondary: '#6b7280',
    colorBorder: '#d7dbe2',
    colorBorderSecondary: '#e5e7eb',
    colorBgLayout: '#f3f4f6',
    colorBgContainer: '#ffffff',
    colorFillAlter: '#f7f8fa',
    borderRadius: 14,
    controlHeight: 40,
    fontFamily:
      '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", sans-serif',
    boxShadowSecondary: '0 18px 40px rgba(33, 29, 24, 0.08)',
  },
  components: {
    Button: {
      controlHeightLG: 44,
      fontWeight: 600,
      primaryShadow: 'none',
    },
    Card: {
      bodyPadding: 20,
    },
    Modal: {
      contentBg: '#fffdf8',
      headerBg: 'transparent',
    },
    Table: {
      headerBg: '#f7f8fa',
      headerColor: '#6b7280',
      borderColor: '#e5e7eb',
    },
  },
};

export async function getInitialState() {
  const token = localStorage.getItem('token');
  const userStr = localStorage.getItem('user');
  
  if (!token || !userStr) {
    return { currentUser: null };
  }

  try {
    const response = await fetch('/api/auth/current', {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await response.json();
    
    if (data.success) {
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
  const isLoginPage = location.pathname === '/login';
  
  if (!token && !isLoginPage) {
    history.push('/login');
  } else if (token && isLoginPage) {
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
