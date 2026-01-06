import { Language, SETTINGS_STORAGE_KEY } from '../settings';
import { TRANSLATIONS } from './translations';

type TranslationValues = Record<string, string | number>;

const PLACEHOLDER_REGEX = /\{(\w+)\}/g;

const interpolate = (template: string, values?: TranslationValues) => {
  if (!values) {
    return template;
  }
  return template.replace(PLACEHOLDER_REGEX, (match, key) => {
    if (!Object.prototype.hasOwnProperty.call(values, key)) {
      return match;
    }
    const value = values[key];
    return value === null || value === undefined ? '' : String(value);
  });
};

export const resolveLocale = (language: Language) => (language === 'zh' ? 'zh-CN' : 'en-US');

export const resolveLanguage = (raw?: string | null): Language => (raw === 'zh' ? 'zh' : 'en');

export const getStoredLanguage = (): Language => {
  if (typeof window === 'undefined') {
    return 'en';
  }
  try {
    const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) {
      return 'en';
    }
    const parsed = JSON.parse(raw) as { profile?: { language?: string } };
    return resolveLanguage(parsed?.profile?.language);
  } catch {
    return 'en';
  }
};

export const translate = (language: Language, message: string, values?: TranslationValues) => {
  const table = TRANSLATIONS[language] ?? {};
  const template = table[message] ?? message;
  return interpolate(template, values);
};

export const createTranslator = (language: Language) =>
  (message: string, values?: TranslationValues) => translate(language, message, values);

export const translateFromStorage = (message: string, values?: TranslationValues) =>
  translate(getStoredLanguage(), message, values);
