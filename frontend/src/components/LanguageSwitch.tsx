import { getLocale, setLocale, useIntl } from '@umijs/max';
import { Select } from 'antd';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import { useEffect } from 'react';
import { getSavedLanguage, LANGUAGE_STORAGE_KEY } from '@/i18n/locale';

interface LanguageSwitchProps {
  className?: string;
}

export default function LanguageSwitch({ className }: LanguageSwitchProps) {
  const intl = useIntl();
  const language = getLocale();
  const savedLanguage = getSavedLanguage();

  useEffect(() => {
    document.documentElement.lang = intl.locale;
    dayjs.locale(intl.locale === 'en-US' ? 'en' : 'zh-cn');
  }, [intl.locale]);

  return (
    <Select
      className={className}
      aria-label={intl.formatMessage({ id: 'app.language' })}
      value={savedLanguage === 'en-US' || savedLanguage === 'zh-CN' ? savedLanguage : 'system'}
      options={[
        { value: 'system', label: intl.formatMessage({ id: 'app.language.system' }) },
        { value: 'zh-CN', label: intl.formatMessage({ id: 'app.language.zh' }) },
        { value: 'en-US', label: intl.formatMessage({ id: 'app.language.en' }) },
      ]}
      onChange={(nextLanguage) => {
        if (nextLanguage === 'system') {
          window.localStorage.removeItem(LANGUAGE_STORAGE_KEY);
          window.location.reload();
        } else if (nextLanguage === language) {
          window.localStorage.setItem(LANGUAGE_STORAGE_KEY, nextLanguage);
          window.location.reload();
        } else {
          setLocale(nextLanguage);
        }
      }}
      size="small"
      popupMatchSelectWidth={false}
    />
  );
}
