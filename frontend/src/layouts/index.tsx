import React from 'react';
import { Outlet } from '@umijs/max';
import Header from '@/components/Header';

export default function Layout() {
  return (
    <div style={{ minHeight: '100vh', background: '#f5f5f5' }}>
      <Header />
      <div style={{ padding: '24px' }}>
        <Outlet />
      </div>
    </div>
  );
}

