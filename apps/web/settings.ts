export type Language = 'en' | 'zh';
export type FontScale = 'sm' | 'md' | 'lg';
export type DefaultView = 'day' | 'week' | 'month' | 'year' | 'all';
export type Provider = 'local' | 'google_photos';

export type SettingsState = {
  profile: {
    displayName: string;
    language: Language;
    photoKey?: string | null;
  };
  preferences: {
    timezone: string;
  };
  appearance: {
    reduceMotion: boolean;
    fontScale: FontScale;
  };
  timeline: {
    defaultView: DefaultView;
    showCaptions: boolean;
    showHighlights: boolean;
    showEpisodes: boolean;
  };
  ingest: {
    defaultProvider: Provider;
    autoDedupe: boolean;
    autoEpisodes: boolean;
    batchLimit: number;
  };
  notifications: {
    weeklySummary: boolean;
  };
  privacy: {
    shareUsage: boolean;
    allowPersonalization: boolean;
  };
  advanced: {
    experimentalFeatures: boolean;
    debugTelemetry: boolean;
  };
};

export const SETTINGS_STORAGE_KEY = 'lifelog.settings';

const resolveLocalTimezone = () => {
  if (typeof Intl === 'undefined') {
    return 'UTC';
  }
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
};

export const getDefaultSettings = (): SettingsState => ({
  profile: {
    displayName: 'Demo User',
    language: 'en',
    photoKey: null,
  },
  preferences: {
    timezone: resolveLocalTimezone(),
  },
  appearance: {
    reduceMotion: false,
    fontScale: 'md',
  },
  timeline: {
    defaultView: 'week',
    showCaptions: true,
    showHighlights: true,
    showEpisodes: true,
  },
  ingest: {
    defaultProvider: 'local',
    autoDedupe: true,
    autoEpisodes: true,
    batchLimit: 40,
  },
  notifications: {
    weeklySummary: false,
  },
  privacy: {
    shareUsage: false,
    allowPersonalization: true,
  },
  advanced: {
    experimentalFeatures: false,
    debugTelemetry: false,
  },
});

export const coerceSettings = (
  raw: Partial<SettingsState> | null | undefined,
  defaults: SettingsState
): SettingsState => {
  if (!raw) {
    return defaults;
  }
  const provider = raw.ingest?.defaultProvider;
  const resolvedProvider =
    provider === 'google_photos' || provider === 'local' ? provider : defaults.ingest.defaultProvider;
  return {
    profile: { ...defaults.profile, ...(raw.profile ?? {}) },
    preferences: { ...defaults.preferences, ...(raw.preferences ?? {}) },
    appearance: { ...defaults.appearance, ...(raw.appearance ?? {}) },
    timeline: { ...defaults.timeline, ...(raw.timeline ?? {}) },
    ingest: { ...defaults.ingest, ...(raw.ingest ?? {}), defaultProvider: resolvedProvider },
    notifications: {
      ...defaults.notifications,
      weeklySummary: raw.notifications?.weeklySummary ?? defaults.notifications.weeklySummary,
    },
    privacy: { ...defaults.privacy, ...(raw.privacy ?? {}) },
    advanced: { ...defaults.advanced, ...(raw.advanced ?? {}) },
  };
};
