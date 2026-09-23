export const LANGUAGE_STORAGE_KEY = 'umi_locale';

export function getSavedLanguage(): string {
  try {
    return window.localStorage.getItem(LANGUAGE_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

export function getPreferredLanguage(): 'zh-CN' | 'en-US' {
  const browserLanguage = typeof navigator === 'undefined'
    ? ''
    : navigator.languages?.[0] || navigator.language || '';
  const language = getSavedLanguage() || browserLanguage;
  return /^en(?:-|$)/i.test(language) ? 'en-US' : 'zh-CN';
}
