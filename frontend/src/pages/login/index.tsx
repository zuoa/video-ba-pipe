import {
  ArrowRightOutlined,
  CheckCircleFilled,
  LockOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { Form, Input, message } from 'antd';
import { history } from '@umijs/max';
import { useState } from 'react';
import Button from '@/components/common/AppButton';
import { SYSTEM_NAME_EN, SYSTEM_NAME_ZH } from '@/constants/branding';
import { login } from '@/services/api';
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
    <main className="login-page">
      <div className="login-shell">
        <section className="login-brand-panel" aria-label="产品简介">
          <header className="login-brand">
            <span className="login-brand__mark" aria-hidden="true">
              <VideoCameraOutlined />
            </span>
            <span>
              <strong>{SYSTEM_NAME_ZH}</strong>
              <small>{SYSTEM_NAME_EN}</small>
            </span>
          </header>

          <div className="login-brand-copy">
            <span className="login-eyebrow">Intelligent video operations</span>
            <h1>
              看见现场，
              <br />
              <span>理解正在发生的一切。</span>
            </h1>
            <p>统一管理视频流、分析任务与告警处置，让每一次异常都有迹可循。</p>
          </div>

          <div className="signal-console" aria-hidden="true">
            <div className="signal-console__header">
              <span className="signal-console__live">
                <i /> Live pipeline
              </span>
              <span>Node 01</span>
            </div>
            <div className="signal-viewport">
              <span className="signal-viewport__scan" />
              <span className="signal-viewport__target signal-viewport__target--primary" />
              <span className="signal-viewport__target signal-viewport__target--secondary" />
              <span className="signal-viewport__coordinate">CH 04 · ANALYSIS ACTIVE</span>
            </div>
            <div className="signal-console__flow">
              <span>视频接入</span>
              <i />
              <span>事件分析</span>
              <i />
              <span>告警闭环</span>
            </div>
          </div>

          <div className="login-system-status">
            <CheckCircleFilled aria-hidden="true" />
            <span>服务通道已就绪</span>
          </div>
        </section>

        <section className="login-access-panel" aria-labelledby="login-title">
          <div className="login-form-wrap">
            <div className="login-mobile-brand">
              <span className="login-brand__mark" aria-hidden="true">
                <VideoCameraOutlined />
              </span>
              <span>
                <strong>{SYSTEM_NAME_ZH}</strong>
                <small>{SYSTEM_NAME_EN}</small>
              </span>
            </div>

            <header className="login-header">
              <span className="login-header__label">安全访问</span>
              <h2 id="login-title">登录控制台</h2>
              <p>请输入您的账号信息以继续使用系统</p>
            </header>

            <Form
              name="system-login"
              layout="vertical"
              requiredMark={false}
              onFinish={onFinish}
              className="login-form"
            >
              <Form.Item
                name="username"
                label="用户名"
                rules={[{ required: true, message: '请输入用户名' }]}
              >
                <Input
                  autoFocus
                  autoComplete="username"
                  prefix={<UserOutlined className="login-input__icon" />}
                  placeholder="请输入用户名"
                  size="large"
                  className="login-input"
                />
              </Form.Item>

              <Form.Item
                name="password"
                label="密码"
                rules={[{ required: true, message: '请输入密码' }]}
              >
                <Input.Password
                  autoComplete="current-password"
                  prefix={<LockOutlined className="login-input__icon" />}
                  placeholder="请输入密码"
                  size="large"
                  className="login-input"
                />
              </Form.Item>

              <Form.Item className="login-submit-item">
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={loading}
                  block
                  size="large"
                  className="login-button"
                >
                  <span>登录系统</span>
                  {!loading ? <ArrowRightOutlined aria-hidden="true" /> : null}
                </Button>
              </Form.Item>
            </Form>

            <div className="login-security-note">
              <SafetyCertificateOutlined aria-hidden="true" />
              <span>账户信息通过安全通道传输</span>
            </div>

            <footer className="login-footer">© 2026 {SYSTEM_NAME_ZH}</footer>
          </div>
        </section>
      </div>
    </main>
  );
}
