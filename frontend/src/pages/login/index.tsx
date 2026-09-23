import {
  ArrowRightOutlined,
  CheckCircleFilled,
  LockOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { Form, Input, message } from 'antd';
import { history, useIntl } from '@umijs/max';
import { useState } from 'react';
import Button from '@/components/common/AppButton';
import { SYSTEM_NAME_EN, SYSTEM_NAME_ZH } from '@/constants/branding';
import { login } from '@/services/api';
import { resetSessionExpiredGuard, resolvePostLoginPath } from '@/utils/auth';
import LanguageSwitch from '@/components/LanguageSwitch';
import './index.css';

export default function Login() {
  const intl = useIntl();
  const t = (id: string) => intl.formatMessage({ id });
  const [loading, setLoading] = useState(false);

  const loginError = (serverMessage?: string) => {
    if (serverMessage === '用户名或密码错误') return t('login.invalidCredentials');
    if (serverMessage === '用户已被禁用') return t('login.accountDisabled');
    if (serverMessage === '用户名和密码不能为空') return t('login.credentialsRequired');
    if (serverMessage && intl.locale === 'zh-CN') return serverMessage;
    if (serverMessage && !/[\u3400-\u9fff]/.test(serverMessage)) return serverMessage;
    return t('app.loginFailed');
  };

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const data = await login(values);

      if (data.success) {
        localStorage.setItem('token', data.token);
        localStorage.setItem('user', JSON.stringify(data.user));
        resetSessionExpiredGuard();
        message.success(t('app.loginSuccess'));
        history.replace(resolvePostLoginPath());
      } else {
        message.error(loginError(data.error));
      }
    } catch (error: any) {
      message.error(loginError(error?.data?.error || error?.response?.data?.error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="login-page">
      <div className="login-shell">
        <section className="login-brand-panel" aria-label={t('login.brandAria')}>
          <header className="login-brand">
            <span className="login-brand__mark" aria-hidden="true">
              <VideoCameraOutlined />
            </span>
            <span>
              <strong>{intl.locale === 'en-US' ? SYSTEM_NAME_EN : SYSTEM_NAME_ZH}</strong>
              <small>{t('app.tagline')}</small>
            </span>
          </header>

          <div className="login-brand-copy">
            <span className="login-eyebrow">{t('login.eyebrow')}</span>
            <h1>
              {t('login.heroFirst')}
              <br />
              <span>{t('login.heroSecond')}</span>
            </h1>
            <p>{t('login.description')}</p>
          </div>

          <div className="signal-console" aria-hidden="true">
            <div className="signal-console__header">
              <span className="signal-console__live">
                <i /> {t('login.livePipeline')}
              </span>
              <span>{t('login.node')}</span>
            </div>
            <div className="signal-viewport">
              <span className="signal-viewport__scan" />
              <span className="signal-viewport__target signal-viewport__target--primary" />
              <span className="signal-viewport__target signal-viewport__target--secondary" />
              <span className="signal-viewport__coordinate">{t('login.analysisActive')}</span>
            </div>
            <div className="signal-console__flow">
              <span>{t('login.videoIngestion')}</span>
              <i />
              <span>{t('login.eventAnalysis')}</span>
              <i />
              <span>{t('login.alertResponse')}</span>
            </div>
          </div>

          <div className="login-system-status">
            <CheckCircleFilled aria-hidden="true" />
            <span>{t('login.serviceReady')}</span>
          </div>
        </section>

        <section className="login-access-panel" aria-labelledby="login-title">
          <div className="login-form-wrap">
            <div className="login-mobile-brand">
              <span className="login-brand__mark" aria-hidden="true">
                <VideoCameraOutlined />
              </span>
              <span>
                <strong>{intl.locale === 'en-US' ? SYSTEM_NAME_EN : SYSTEM_NAME_ZH}</strong>
                <small>{t('app.tagline')}</small>
              </span>
            </div>

            <header className="login-header">
              <span className="login-header__label">{t('login.secureAccess')}</span>
              <h2 id="login-title">{t('login.title')}</h2>
              <p>{t('login.instructions')}</p>
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
                label={t('login.username')}
                rules={[{ required: true, message: t('login.usernameRequired') }]}
              >
                <Input
                  autoFocus
                  autoComplete="username"
                  prefix={<UserOutlined className="login-input__icon" />}
                  placeholder={t('login.usernameRequired')}
                  size="large"
                  className="login-input"
                />
              </Form.Item>

              <Form.Item
                name="password"
                label={t('login.password')}
                rules={[{ required: true, message: t('login.passwordRequired') }]}
              >
                <Input.Password
                  autoComplete="current-password"
                  prefix={<LockOutlined className="login-input__icon" />}
                  placeholder={t('login.passwordRequired')}
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
                  <span>{t('login.submit')}</span>
                  {!loading ? <ArrowRightOutlined aria-hidden="true" /> : null}
                </Button>
              </Form.Item>
            </Form>

            <div className="login-security-note">
              <SafetyCertificateOutlined aria-hidden="true" />
              <span>{t('login.securityNote')}</span>
            </div>

            <footer className="login-footer">
              <LanguageSwitch className="login-language-switch" />
              <span>© 2026 {intl.locale === 'en-US' ? SYSTEM_NAME_EN : SYSTEM_NAME_ZH}</span>
            </footer>
          </div>
        </section>
      </div>
    </main>
  );
}
