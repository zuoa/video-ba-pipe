import React from 'react';
import { ConfigProvider } from 'antd';
import Header from './components/Header';

export const request = {
  timeout: 10000,
  errorConfig: {
    errorHandler() {},
    errorThrower() {},
  },
  requestInterceptors: [],
  responseInterceptors: [],
};

export const layout = {
  navTheme: 'light',
  layout: 'top',
  headerRender: () => <Header />,
};

export function rootContainer(container: React.ReactElement) {
  return React.createElement(
    ConfigProvider,
    {
      theme: {
        token: {
          colorPrimary: '#000000',
        },
      },
    },
    container
  );
}
