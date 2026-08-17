import React, { useCallback, useEffect, useState } from 'react';
import { Alert } from 'antd';
import { getLicenseStatus, LicenseStatus } from '@/services/api';

export const LICENSE_UPDATED_EVENT = 'video-ba-license-updated';

const LicenseBanner: React.FC = () => {
  const [status, setStatus] = useState<LicenseStatus | null>(null);

  const refresh = useCallback(() => {
    if (!localStorage.getItem('token')) {
      setStatus(null);
      return;
    }
    getLicenseStatus().then(setStatus).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    window.addEventListener(LICENSE_UPDATED_EVENT, refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(LICENSE_UPDATED_EVENT, refresh);
    };
  }, [refresh]);

  if (!status || status.tier === 'licensed') return null;

  const reason = status.license_status === 'missing'
    ? '当前为永久免费试用版'
    : `付费许可证不可用（${status.message}），已自动降级`;
  return (
    <Alert
      className="license-global-banner"
      type="warning"
      showIcon
      message={reason}
      description={`当前额度：${status.limits.video_sources} 路视频源、${status.limits.algorithms} 个算法。超出额度的数据会保留，但不参与运行。`}
    />
  );
};

export default LicenseBanner;
