import { defineConfig } from '@umijs/max';

export default defineConfig({
  antd: {
    configProvider: {
      theme: {
        token: {
          colorPrimary: '#000000',
        },
      },
    },
  },
  access: {},
  model: {},
  initialState: {},
  request: {},
  layout: false,
  routes: [
    {
      path: '/login',
      component: './login',
      layout: false,
    },
    {
      path: '/',
      redirect: '/dashboard',
    },
    {
      name: '仪表板',
      path: '/dashboard',
      component: './dashboard',
      icon: 'DashboardOutlined',
    },
    {
      name: '工作流',
      path: '/workflows',
      component: './workflows',
      icon: 'ApartmentOutlined',
    },
    {
      name: '工作流编辑器',
      path: '/workflows/editor/:id',
      component: './workflows/editor',
      layout: false,
    },
    {
      name: '算法',
      path: '/algorithms',
      component: './algorithms',
      icon: 'ExperimentOutlined',
    },
    {
      name: '算法配置向导',
      path: '/algorithms/wizard',
      component: './algorithms/wizard',
    },
    {
      name: '视频源',
      path: '/video-sources',
      component: './video-sources',
      icon: 'VideoCameraOutlined',
    },
    {
      name: '告警',
      path: '/alerts',
      component: './alerts',
      icon: 'AlertOutlined',
    },
    {
      name: '告警大屏',
      path: '/alert-wall',
      component: './alert-wall',
      icon: 'DesktopOutlined',
      layout: false,
    },
    {
      name: '模型',
      path: '/models',
      component: './model-management',
      icon: 'FunctionOutlined',
    },
    {
      name: '脚本',
      path: '/scripts',
      component: './scripts',
      icon: 'CodeOutlined',
    },
    {
      name: '用户管理',
      path: '/users',
      component: './users',
      icon: 'UserOutlined',
    },
  ],
  npmClient: 'npm',
  proxy: {
    '/api': {
      target: 'http://10.0.4.147:5002',
      changeOrigin: true,
      // Avoid HPM HPE_UNEXPECTED_CONTENT_LENGTH for range video responses
      onProxyReq: (proxyReq) => {
        proxyReq.removeHeader('accept-encoding');
      },
      onProxyRes: (proxyRes) => {
        const hasTE = !!proxyRes.headers['transfer-encoding'];
        if (hasTE && proxyRes.headers['content-length']) {
          delete proxyRes.headers['content-length'];
        }
      },
    },
  },
  esbuildMinifyIIFE: true,
});
