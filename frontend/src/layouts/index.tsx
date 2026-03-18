import React from 'react';
import { Outlet, useLocation } from '@umijs/max';
import Header from '@/components/Header';

export default function Layout() {
  const location = useLocation();
  const isStandalonePage =
    location.pathname === '/login' || location.pathname === '/alert-wall';

  if (isStandalonePage) {
    return <Outlet />;
  }

  return (
    <div className="app-shell">
      <Header />
      <main className="app-shell__main">
        <div className="app-shell__content">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
