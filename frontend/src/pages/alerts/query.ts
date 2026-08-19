import { AlertFilter } from './types';

export function buildAlertQueryParams(
  filter: AlertFilter,
  customTimeRange?: { start: string; end: string },
): AlertFilter {
  const params: AlertFilter = { ...filter };

  if (params.time_range && params.time_range !== 'custom') {
    const now = new Date();
    let startTime: Date;

    switch (params.time_range) {
      case '1h':
        startTime = new Date(now.getTime() - 60 * 60 * 1000);
        break;
      case '24h':
        startTime = new Date(now.getTime() - 24 * 60 * 60 * 1000);
        break;
      case '7d':
        startTime = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
        break;
      case '30d':
        startTime = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
        break;
      default:
        startTime = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    }

    params.start_time = startTime.toISOString();
    params.end_time = now.toISOString();
    delete params.time_range;
  } else if (params.time_range === 'custom' && customTimeRange) {
    params.start_time = customTimeRange.start;
    params.end_time = customTimeRange.end;
    delete params.time_range;
  } else {
    delete params.time_range;
  }

  Object.keys(params).forEach((key) => {
    const value = params[key as keyof AlertFilter];
    if (value === '' || value === undefined) {
      delete params[key as keyof AlertFilter];
    }
  });

  return params;
}
