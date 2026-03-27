const normalizeConfidence = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.max(0, Math.min(1, value));
  }
  return undefined;
};

export const getAlgorithmDefaultConfidence = (algorithm?: any): number | undefined => {
  if (!algorithm) {
    return undefined;
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
