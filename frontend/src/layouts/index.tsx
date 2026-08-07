import React from 'react';
import { Outlet, useLocation } from '@umijs/max';
import Header from '@/components/Header';
import ErrorBoundary from '@/components/common/ErrorBoundary';

export default function Layout() {
  const location = useLocation();
  const isStandalonePage =
    location.pathname === '/login' || location.pathname === '/alert-wall';

  if (isStandalonePage) {
    return (
      <ErrorBoundary>
        <Outlet />
      </ErrorBoundary>
    );
  }

  return (
    <div className="app-shell">
      <Header />
      <main className="app-shell__main">
        <div className="app-shell__content">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}
