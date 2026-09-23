import { tr, trf } from '@/i18n/tr';
import React from 'react';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';

dayjs.extend(relativeTime);

interface RelativeTimeProps {
  time: string | Date;
  className?: string;
  showFullTime?: boolean;
}

const RelativeTime: React.FC<RelativeTimeProps> = ({ time, className, showFullTime = false }) => {
  const date = dayjs(time);
  const now = dayjs();
  const diffMinutes = now.diff(date, 'minute');
  const diffHours = now.diff(date, 'hour');
  const diffDays = now.diff(date, 'day');

  let relativeTime = '';
  let fullTime = date.format('YYYY-MM-DD HH:mm:ss');

  if (diffMinutes < 1) {
    relativeTime = tr("刚刚");
  } else if (diffMinutes < 60) {
    relativeTime = trf("__VAR0__分钟前", [diffMinutes]);
  } else if (diffHours < 24) {
    relativeTime = trf("__VAR0__小时前", [diffHours]);
  } else if (diffDays < 7) {
    relativeTime = trf("__VAR0__天前", [diffDays]);
  } else {
    relativeTime = date.format('YYYY-MM-DD');
  }

  return (
    <span className={className}>
      {showFullTime ? (
        <span>
          <span style={{ fontWeight: 500 }}>{relativeTime}</span>
          <span style={{ color: '#999', marginLeft: 8 }}>{date.format('HH:mm:ss')}</span>
        </span>
      ) : (
        relativeTime
      )}
    </span>
  );
};

export default RelativeTime;
