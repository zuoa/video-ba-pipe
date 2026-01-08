import { history } from '@umijs/max';
import { message } from 'antd';

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
