const TRACKER_SCRIPT_MARKERS = ['object_tracker.py', 'loiter_analyzer.py'];

const normalizeConfidence = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.max(0, Math.min(1, value));
  }
  return undefined;
};

export const parseAlgorithmScriptConfig = (algorithm?: any): Record<string, any> => {
  try {
    const raw = algorithm?.script_config;
    if (!raw) return {};
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch (error) {
    console.warn('解析算法 script_config 失败:', algorithm?.id, error);
    return {};
  }
};

export const isTrackerAlgorithm = (algorithm?: any): boolean => {
  const path = String(algorithm?.script_path || '');
  return TRACKER_SCRIPT_MARKERS.some((marker) => path.includes(marker));
};

const firstFiniteNumber = (...values: unknown[]): number | undefined => {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === 'string' && value.trim() !== '') {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return undefined;
};

export const getTrackerEventFormValues = (algorithm?: any, nodeConfig: Record<string, any> = {}) => {
  const scriptConfig = parseAlgorithmScriptConfig(algorithm);
  const fallbackEvent = String(algorithm?.script_path || '').includes('loiter_analyzer.py') ? 'loiter' : 'none';
  const event = String(nodeConfig.event ?? scriptConfig.event ?? fallbackEvent);
  const maxDisplaceDefault = event === 'stay' ? 48 : undefined;
  return {
    trackEvent: event,
    minDwellSeconds: firstFiniteNumber(nodeConfig.min_dwell_seconds, scriptConfig.min_dwell_seconds, 8),
    minDisplacePx: firstFiniteNumber(nodeConfig.min_displace_px, scriptConfig.min_displace_px, 0),
    maxDisplacePx: firstFiniteNumber(nodeConfig.max_displace_px, scriptConfig.max_displace_px, maxDisplaceDefault),
    disappearSeconds: firstFiniteNumber(nodeConfig.disappear_seconds, scriptConfig.disappear_seconds, 3),
    crossMode: nodeConfig.cross_mode || scriptConfig.cross_mode || 'enter',
    crossDirection: nodeConfig.cross_direction || scriptConfig.cross_direction || 'any',
  };
};

export const trackerEventConfigFromForm = (values: Record<string, any>) => {
  const event = values.trackEvent || 'none';
  const config: Record<string, any> = { event };
  if (event === 'loiter' || event === 'stay') {
    config.min_dwell_seconds = values.minDwellSeconds ?? 8;
    config.min_displace_px = values.minDisplacePx ?? 0;
    config.max_displace_px = values.maxDisplacePx ?? (event === 'stay' ? 48 : null);
    config.disappear_seconds = values.disappearSeconds ?? 3;
  } else if (event === 'region_cross') {
    config.cross_mode = values.crossMode || 'enter';
    config.cross_direction = values.crossDirection || 'any';
    config.disappear_seconds = values.disappearSeconds ?? 3;
  }
  return config;
};

export const getAlgorithmDefaultConfidence = (algorithm?: any): number | undefined => {
  if (!algorithm) {
    return undefined;
  }

  if (algorithm.algorithm_type === 'ocr') {
    return normalizeConfidence(algorithm?.ocr_config?.recognition_score_threshold);
  }

  try {
    const scriptConfig = JSON.parse(algorithm?.script_config || '{}');
    const topLevelConfidence = normalizeConfidence(scriptConfig?.confidence);
    const overrideEnabled = Boolean(scriptConfig?.confidence_override_enabled);
    const models = scriptConfig?.models;

    if (Array.isArray(models) && models.length === 1) {
      const modelConfidence = normalizeConfidence(models[0]?.confidence);
      if (overrideEnabled) {
        return topLevelConfidence ?? modelConfidence;
      }
      if (topLevelConfidence === undefined) {
        return modelConfidence;
      }
      if (modelConfidence === undefined) {
        return topLevelConfidence;
      }
      return Math.max(topLevelConfidence, modelConfidence);
    }

    return topLevelConfidence;
  } catch (error) {
    console.warn('解析算法 script_config 失败:', algorithm?.id, error);
    return undefined;
  }
};
