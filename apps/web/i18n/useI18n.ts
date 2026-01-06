import { useMemo } from 'react';
import { useSettings } from '../contexts/SettingsContext';
import { createTranslator, resolveLocale } from './core';

export const useI18n = () => {
  const { settings } = useSettings();
  const language = settings.profile.language;
  const t = useMemo(() => createTranslator(language), [language]);
  const locale = resolveLocale(language);

  return { t, language, locale };
};
