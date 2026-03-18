import { LockOutlined, UserOutlined, VideoCameraOutlined, SafetyOutlined } from '@ant-design/icons';
import { Button, Form, Input, message } from 'antd';
import { useState } from 'react';
import { history } from '@umijs/max';
import { login } from '@/services/api';
import { SYSTEM_NAME_EN, SYSTEM_NAME_ZH } from '@/constants/branding';
import './index.css';

export default function Login() {
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const data = await login(values);
      
      if (data.success) {
        localStorage.setItem('token', data.token);
        localStorage.setItem('user', JSON.stringify(data.user));
        message.success('登录成功');
        history.push('/dashboard');
      } else {
        message.error(data.error || '登录失败');
      }
    } catch (error) {
      message.error('登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-container">
      <div className="login-wrapper">
        <div className="login-left">
          <div className="brand-section">
            <div className="logo-wrapper">
              <VideoCameraOutlined className="logo-icon" />
            </div>
            <h1 className="brand-title">{SYSTEM_NAME_ZH}</h1>
            <p className="brand-subtitle">{SYSTEM_NAME_EN}</p>
            <div className="features">
              <div className="feature-item">
                <SafetyOutlined />
                <span>实时识别</span>
              </div>
              <div className="feature-item">
                <VideoCameraOutlined />
                <span>流式监控</span>
              </div>
              <div className="feature-item">
                <SafetyOutlined />
                <span>告警追踪</span>
              </div>
            </div>
          </div>
          <div className="decorative-circles">
            <div className="circle circle-1"></div>
            <div className="circle circle-2"></div>
            <div className="circle circle-3"></div>
          </div>
        </div>
        
        <div className="login-right">
          <div className="login-box">
            <div className="login-header">
              <h2>欢迎回来</h2>
              <p>登录以继续使用系统</p>
            </div>
            
            <Form onFinish={onFinish} autoComplete="off" className="login-form">
              <Form.Item
                name="username"
                rules={[{ required: true, message: '请输入用户名' }]}
              >
                <Input 
                  prefix={<UserOutlined className="input-icon" />} 
                  placeholder="用户名" 
                  size="large"
                  className="login-input"
                />
              </Form.Item>
              
              <Form.Item
                name="password"
                rules={[{ required: true, message: '请输入密码' }]}
              >
                <Input.Password
                  prefix={<LockOutlined className="input-icon" />}
                  placeholder="密码"
                  size="large"
                  className="login-input"
                />
              </Form.Item>
              
              <Form.Item>
                <Button 
                  type="primary" 
                  htmlType="submit" 
                  loading={loading} 
                  block 
                  size="large"
                  className="app-primary-button login-button"
                >
                  登录
                </Button>
              </Form.Item>
            </Form>
            
            <div className="login-footer">
              <span>© 2026 {SYSTEM_NAME_ZH}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
