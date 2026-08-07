import React from 'react';
import { Result, Button } from 'antd';

interface ErrorBoundaryProps {
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error?: Error;
}

/**
 * 全局渲染兜底:子树渲染抛错时不再整页白屏,而是显示「出错 + 重试/返回首页」。
 * 注:React 的错误边界必须是 class 组件。
 */
class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // 便于线上排查,同时不影响渲染兜底页
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary] 渲染崩溃:', error, info.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: undefined });
  };

  handleGoHome = () => {
    // 整页刷新回首页,清掉残留的异常状态
    window.location.assign('/');
  };

  render() {
    if (this.state.hasError) {
      return (
        <Result
          status="error"
          title="页面出错了"
          subTitle="渲染过程中发生异常。可以尝试重试,或返回首页后重新进入该页面。"
          extra={[
            <Button key="retry" onClick={this.handleRetry}>
              重试
            </Button>,
            <Button key="home" type="primary" onClick={this.handleGoHome}>
              返回首页
            </Button>,
          ]}
        />
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
