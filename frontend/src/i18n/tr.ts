import { getPreferredLanguage } from './locale';
import { uiEnglish } from './ui-en';
import { uiTemplatesEnglish } from './ui-templates-en';

export function getDateLocale(): 'zh-CN' | 'en-US' {
  return getPreferredLanguage();
}

/** Translate Chinese source copy in existing pages without changing API values. */
export function tr(source: string): string {
  return getPreferredLanguage() === 'en-US' ? (uiEnglish[source] || source) : source;
}

/** Format localized sentences while preserving the source sentence's variables. */
export function trf(source: string, values: readonly unknown[]): string {
  const template = getPreferredLanguage() === 'en-US'
    ? (uiTemplatesEnglish[source] || source)
    : source;
  return template.replace(/__VAR(\d+)__/g, (_, index: string) => String(values[Number(index)]));
}
