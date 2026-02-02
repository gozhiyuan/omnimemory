import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { apiGet, apiPut } from '../services/api';
import { translateFromStorage } from '../i18n/core';
import { SettingsState, SETTINGS_STORAGE_KEY, coerceSettings, getDefaultSettings } from '../settings';
import { useAuth } from './AuthContext';

type SettingsContextValue = {
  settings: SettingsState;
  loading: boolean;
  error: string | null;
  refreshSettings: () => Promise<void>;
  saveSettings: (next: SettingsState) => Promise<void>;
};

type SettingsResponse = {
  settings: Partial<SettingsState> | null;
};

const SettingsContext = createContext<SettingsContextValue | null>(null);

const loadCachedSettings = (defaults: SettingsState) => {
  if (typeof window === 'undefined') {
    return defaults;
  }
  try {
    const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) {
      return defaults;
    }
    return coerceSettings(JSON.parse(raw) as Partial<SettingsState>, defaults);
  } catch {
    return defaults;
  }
};

const storeCachedSettings = (settings: SettingsState) => {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Ignore localStorage failures.
  }
};

export const SettingsProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { authEnabled, isAuthenticated, loading: authLoading } = useAuth();
  const defaults = useMemo(() => getDefaultSettings(), []);
  const [settings, setSettings] = useState<SettingsState>(() => loadCachedSettings(defaults));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const autoSyncedTimezoneRef = useRef(false);

  const refreshSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiGet<SettingsResponse>('/settings');
      const merged = coerceSettings(response.settings ?? null, defaults);
      setSettings(merged);
      storeCachedSettings(merged);
      const storedTimezone = response.settings?.preferences?.timezone;
      if (!storedTimezone && merged.preferences.timezone && !autoSyncedTimezoneRef.current) {
        autoSyncedTimezoneRef.current = true;
        try {
          await apiPut('/settings', { settings: merged });
          storeCachedSettings(merged);
        } catch {
          // Ignore background sync failures; user can save manually.
        }
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : translateFromStorage('Unable to load settings.')
      );
    } finally {
      setLoading(false);
    }
  }, [defaults]);

  const saveSettings = useCallback(
    async (next: SettingsState) => {
      setLoading(true);
      setError(null);
      try {
        await apiPut('/settings', { settings: next });
        setSettings(next);
        storeCachedSettings(next);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : translateFromStorage('Unable to save settings.')
        );
        throw err;
      } finally {
        setLoading(false);
      }
    },
    []
  );

  const value = useMemo(
    () => ({
      settings,
      loading,
      error,
      refreshSettings,
      saveSettings,
    }),
    [settings, loading, error, refreshSettings, saveSettings]
  );

  useEffect(() => {
    if (authEnabled) {
      if (authLoading || !isAuthenticated) {
        return;
      }
    }
    void refreshSettings();
  }, [authEnabled, authLoading, isAuthenticated, refreshSettings]);

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
};

export const useSettings = () => {
  const ctx = useContext(SettingsContext);
  if (!ctx) {
    throw new Error('useSettings must be used within SettingsProvider');
  }
  return ctx;
};
