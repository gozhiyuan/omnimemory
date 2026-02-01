import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Bell, Calendar, Cloud, Cpu, KeyRound, Lock, Sliders, UploadCloud, UserCircle } from 'lucide-react';
import { PageMotion } from './PageMotion';
import { apiDelete, apiGet, apiPost } from '../services/api';
import { toast } from '../services/toast';
import {
  ApiKeyCreateResponse,
  ApiKeyInfo,
  ApiKeyListResponse,
  DashboardStatsResponse,
  GooglePhotosAuthUrlResponse,
  GooglePhotosStatus,
  UploadUrlResponse,
} from '../types';
import { useSettings } from '../contexts/SettingsContext';
import { useAuth } from '../contexts/AuthContext';
import { useI18n } from '../i18n/useI18n';
import {
  DefaultView,
  FontScale,
  Language,
  Provider,
  SettingsState,
} from '../settings';

const inputClass =
  'mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 shadow-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';
const inputDisabledClass =
  `${inputClass} disabled:cursor-not-allowed disabled:opacity-60`;

type DownloadUrlResponse = {
  key: string;
  url: string;
};

const SectionCard: React.FC<{
  title: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}> = ({ title, description, icon, action, className, children }) => (
  <div
    className={`rounded-2xl border border-white/70 bg-white/80 p-6 shadow-sm backdrop-blur dark:border-slate-800 dark:bg-slate-900/60 ${
      className ?? ''
    }`}
  >
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex items-start gap-3">
        {icon && (
          <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-xl bg-primary-50 text-primary-600 dark:bg-slate-800 dark:text-primary-300">
            {icon}
          </div>
        )}
        <div>
          <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
          {description && (
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{description}</p>
          )}
        </div>
      </div>
      {action}
    </div>
    <div className="mt-4 space-y-4">{children}</div>
  </div>
);

const ToggleRow: React.FC<{
  label: string;
  description?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}> = ({ label, description, checked, onChange, disabled }) => (
  <label
    className={`flex items-center justify-between gap-4 rounded-lg border border-slate-100 bg-white/70 px-4 py-3 text-sm text-slate-700 shadow-sm dark:border-slate-800 dark:bg-slate-950/40 dark:text-slate-200 ${
      disabled ? 'cursor-not-allowed opacity-60' : ''
    }`}
  >
    <div>
      <p className="font-medium">{label}</p>
      {description && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{description}</p>}
    </div>
    <input
      type="checkbox"
      checked={checked}
      onChange={(event) => onChange(event.target.checked)}
      disabled={disabled}
      className="h-4 w-4 accent-primary-600"
    />
  </label>
);

export const Settings: React.FC = () => {
  const { settings, loading: settingsLoading, error: settingsError, saveSettings } = useSettings();
  const { user: authUser } = useAuth();
  const { t } = useI18n();
  const apiBase = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
  const [draft, setDraft] = useState<SettingsState>(settings);
  const [saving, setSaving] = useState(false);
  const [googleStatus, setGoogleStatus] = useState<GooglePhotosStatus | null>(null);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [googleError, setGoogleError] = useState<string | null>(null);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [photoUploading, setPhotoUploading] = useState(false);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const [apiKeys, setApiKeys] = useState<ApiKeyInfo[]>([]);
  const [apiKeysLoading, setApiKeysLoading] = useState(false);
  const [apiKeysError, setApiKeysError] = useState<string | null>(null);
  const [creatingKey, setCreatingKey] = useState(false);
  const [createKeyName, setCreateKeyName] = useState('OpenClaw');
  const [createKeyExpiry, setCreateKeyExpiry] = useState('0');
  const [createdKey, setCreatedKey] = useState<ApiKeyCreateResponse | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    setDraft(settings);
  }, [settings]);

  const formatLocalTime = (value?: string | null) => {
    if (!value) {
      return t('Not connected');
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return t('Unknown');
    }
    return parsed.toLocaleString();
  };

  const copyToClipboard = async (value: string, label: string) => {
    if (!value) {
      return;
    }
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        const area = document.createElement('textarea');
        area.value = value;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.focus();
        area.select();
        document.execCommand('copy');
        document.body.removeChild(area);
      }
      toast.success(t('Copied'), t('{label} copied to clipboard.', { label }));
    } catch (err) {
      toast.error(t('Copy failed'), err instanceof Error ? err.message : t('Try again.'));
    }
  };

  useEffect(() => {
    if (!draft.profile.photoKey) {
      setPhotoUrl(null);
      return;
    }
    let active = true;
    const resolveUrl = async () => {
      try {
        const download = await apiPost<DownloadUrlResponse>('/storage/download-url', {
          key: draft.profile.photoKey,
        });
        if (active) {
          setPhotoUrl(download.url);
        }
      } catch (err) {
        if (active) {
          setPhotoError(err instanceof Error ? err.message : t('Unable to load photo.'));
        }
      }
    };
    void resolveUrl();
    return () => {
      active = false;
    };
  }, [draft.profile.photoKey]);

  const fetchStatus = useCallback(async () => {
    setGoogleLoading(true);
    setGoogleError(null);
    try {
      const status = await apiGet<GooglePhotosStatus>('/integrations/google/photos/status');
      if (mountedRef.current) {
        setGoogleStatus(status);
      }
    } catch (err) {
      if (mountedRef.current) {
        setGoogleError(err instanceof Error ? err.message : t('Unable to fetch status.'));
      }
    } finally {
      if (mountedRef.current) {
        setGoogleLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void fetchStatus();
  }, [fetchStatus]);

  const fetchApiKeys = useCallback(async () => {
    setApiKeysLoading(true);
    setApiKeysError(null);
    try {
      const response = await apiGet<ApiKeyListResponse>('/settings/api-keys');
      setApiKeys(response.keys ?? []);
    } catch (err) {
      setApiKeysError(err instanceof Error ? err.message : t('Unable to load API keys.'));
    } finally {
      setApiKeysLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void fetchApiKeys();
  }, [fetchApiKeys]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await saveSettings(draft);
      toast.success(t('Settings saved'), t('Your preferences are now synced.'));
    } catch {
      toast.error(t('Unable to save settings'), t('Please try again.'));
    } finally {
      setSaving(false);
    }
  };

  const updateProfile = <K extends keyof SettingsState['profile']>(
    key: K,
    value: SettingsState['profile'][K]
  ) => {
    setDraft((prev) => ({ ...prev, profile: { ...prev.profile, [key]: value } }));
  };

  const updateAppearance = <K extends keyof SettingsState['appearance']>(
    key: K,
    value: SettingsState['appearance'][K]
  ) => {
    setDraft((prev) => ({ ...prev, appearance: { ...prev.appearance, [key]: value } }));
  };

  const updatePreferences = <K extends keyof SettingsState['preferences']>(
    key: K,
    value: SettingsState['preferences'][K]
  ) => {
    setDraft((prev) => ({ ...prev, preferences: { ...prev.preferences, [key]: value } }));
  };

  const updateTimeline = <K extends keyof SettingsState['timeline']>(
    key: K,
    value: SettingsState['timeline'][K]
  ) => {
    setDraft((prev) => ({ ...prev, timeline: { ...prev.timeline, [key]: value } }));
  };

  const updateIngest = <K extends keyof SettingsState['ingest']>(
    key: K,
    value: SettingsState['ingest'][K]
  ) => {
    setDraft((prev) => ({ ...prev, ingest: { ...prev.ingest, [key]: value } }));
  };

  const updateNotifications = (value: boolean) => {
    setDraft((prev) => ({
      ...prev,
      notifications: { ...prev.notifications, weeklySummary: value },
    }));
  };

  const updatePrivacy = <K extends keyof SettingsState['privacy']>(
    key: K,
    value: SettingsState['privacy'][K]
  ) => {
    setDraft((prev) => ({ ...prev, privacy: { ...prev.privacy, [key]: value } }));
  };

  const updateAdvanced = <K extends keyof SettingsState['advanced']>(
    key: K,
    value: SettingsState['advanced'][K]
  ) => {
    setDraft((prev) => ({ ...prev, advanced: { ...prev.advanced, [key]: value } }));
  };

  const updateOpenClaw = <K extends keyof SettingsState['openclaw']>(
    key: K,
    value: SettingsState['openclaw'][K]
  ) => {
    setDraft((prev) => ({ ...prev, openclaw: { ...prev.openclaw, [key]: value } }));
  };

  const handleConnect = async () => {
    try {
      const response = await apiGet<GooglePhotosAuthUrlResponse>('/integrations/google/photos/auth-url');
      window.location.href = response.auth_url;
    } catch (err) {
      toast.error(
        t('Unable to start connection'),
        err instanceof Error ? err.message : t('Try again later.')
      );
    }
  };

  const handleDisconnect = async () => {
    try {
      await apiPost('/integrations/google/photos/disconnect');
      toast.success(t('Disconnected'), t('Google Photos access removed.'));
      void fetchStatus();
    } catch (err) {
      toast.error(
        t('Unable to disconnect'),
        err instanceof Error ? err.message : t('Try again later.')
      );
    }
  };

  const handleCreateKey = async () => {
    const name = createKeyName.trim();
    if (!name) {
      toast.error(t('Invalid name'), t('Please provide a name for the key.'));
      return;
    }
    setCreatingKey(true);
    try {
      const expiresInDays = Number(createKeyExpiry);
      const payload =
        Number.isFinite(expiresInDays) && expiresInDays > 0
          ? { name, expires_in_days: expiresInDays }
          : { name };
      const response = await apiPost<ApiKeyCreateResponse>('/settings/api-keys', payload);
      setCreatedKey(response);
      toast.success(t('API key created'), t('Copy the key now — it will only be shown once.'));
      void fetchApiKeys();
    } catch (err) {
      toast.error(t('Unable to create key'), err instanceof Error ? err.message : t('Try again.'));
    } finally {
      setCreatingKey(false);
    }
  };

  const handleRevokeKey = async (key: ApiKeyInfo) => {
    if (!window.confirm(t('Revoke API key "{name}"?', { name: key.name }))) {
      return;
    }
    try {
      await apiDelete(`/settings/api-keys/${key.id}`);
      toast.success(t('API key revoked'), t('Key "{name}" has been revoked.', { name: key.name }));
      void fetchApiKeys();
    } catch (err) {
      toast.error(t('Unable to revoke key'), err instanceof Error ? err.message : t('Try again.'));
    }
  };

  const handlePhotoUpload = async (file: File) => {
    if (!file) {
      return;
    }
    setPhotoUploading(true);
    setPhotoError(null);
    try {
      const contentType = file.type || 'application/octet-stream';
      const uploadMeta = await apiPost<UploadUrlResponse>('/storage/upload-url', {
        filename: file.name,
        content_type: contentType,
        prefix: 'avatars',
      });
      const headers = { ...(uploadMeta.headers || {}), 'Content-Type': contentType };
      const uploadResponse = await fetch(uploadMeta.url, {
        method: 'PUT',
        headers,
        body: file,
      });
      if (!uploadResponse.ok) {
        const responseText = await uploadResponse.text();
        throw new Error(
          t('Upload failed: {status} {details}', {
            status: uploadResponse.status,
            details: responseText || '',
          }).trim()
        );
      }
      setDraft((prev) => ({
        ...prev,
        profile: { ...prev.profile, photoKey: uploadMeta.key },
      }));
      const download = await apiPost<DownloadUrlResponse>('/storage/download-url', {
        key: uploadMeta.key,
      });
      setPhotoUrl(download.url);
    } catch (err) {
      setPhotoError(err instanceof Error ? err.message : t('Failed to upload photo.'));
    } finally {
      setPhotoUploading(false);
    }
  };

  const handlePhotoClear = () => {
    setDraft((prev) => ({ ...prev, profile: { ...prev.profile, photoKey: null } }));
    setPhotoUrl(null);
  };

  const [usageStats, setUsageStats] = useState<DashboardStatsResponse | null>(null);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [usageLoading, setUsageLoading] = useState(false);

  useEffect(() => {
    let active = true;
    const fetchUsage = async () => {
      setUsageLoading(true);
      setUsageError(null);
      try {
        const data = await apiGet<DashboardStatsResponse>('/dashboard/stats');
        if (active) {
          setUsageStats(data);
        }
      } catch (err) {
        if (active) {
          setUsageError(err instanceof Error ? err.message : t('Failed to load dashboard.'));
        }
      } finally {
        if (active) {
          setUsageLoading(false);
        }
      }
    };
    void fetchUsage();
    return () => {
      active = false;
    };
  }, [t]);

  const formatStorage = useCallback(
    (bytes: number) => {
      if (!Number.isFinite(bytes) || bytes <= 0) {
        return t('—');
      }
      const gb = bytes / (1024 * 1024 * 1024);
      if (gb >= 1) {
        return t('{value} GB', { value: gb.toFixed(1) });
      }
      const mb = bytes / (1024 * 1024);
      return `${mb.toFixed(1)} MB`;
    },
    [t]
  );

  const usageSummary = useMemo(() => {
    if (usageLoading) {
      return [
        { label: t('Total items'), value: t('Loading...') },
        { label: t('Storage used'), value: t('Loading...') },
        { label: t('Active connections'), value: t('Loading...') },
      ];
    }
    if (!usageStats) {
      return [
        { label: t('Total items'), value: '—' },
        { label: t('Storage used'), value: '—' },
        { label: t('Active connections'), value: '—' },
      ];
    }
    return [
      { label: t('Total items'), value: String(usageStats.total_items) },
      { label: t('Storage used'), value: formatStorage(usageStats.storage_used_bytes) },
      { label: t('Active connections'), value: String(usageStats.active_connections) },
    ];
  }, [formatStorage, t, usageLoading, usageStats]);

  const timezoneOptions = useMemo(() => {
    if (typeof Intl !== 'undefined' && 'supportedValuesOf' in Intl) {
      const values = (Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] })
        .supportedValuesOf?.('timeZone');
      if (values && values.length > 0) {
        return values;
      }
    }
    return [
      'UTC',
      'America/Los_Angeles',
      'America/New_York',
      'Europe/London',
      'Europe/Paris',
      'Asia/Shanghai',
      'Asia/Tokyo',
      'Asia/Singapore',
      'Australia/Sydney',
    ];
  }, []);

  return (
    <PageMotion className="h-full overflow-y-auto p-8 space-y-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
            {t('Settings')}
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {t('Personalize your OmniMemory experience. Changes here apply to your account.')}
          </p>
        </div>
        <button
          type="button"
          onClick={handleSave}
          className="rounded-full bg-primary-600 px-5 py-2 text-sm font-semibold text-white shadow-sm hover:bg-primary-500"
          disabled={settingsLoading || saving}
        >
          {saving ? t('Saving...') : t('Save changes')}
        </button>
      </header>

      {settingsError && (
        <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {t('Settings load error: {message}', { message: settingsError })}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-2">
        <SectionCard
          title={t('Profile')}
          description={t('Update how your memories are labeled and organized.')}
          icon={<UserCircle size={18} />}
        >
          <div className="flex flex-wrap items-center gap-4">
            <div className="h-16 w-16 overflow-hidden rounded-2xl border border-slate-200 bg-slate-100">
              {photoUrl ? (
                <img
                  src={photoUrl}
                  alt={t('Profile')}
                  className="h-full w-full object-cover"
                  loading="lazy"
                />
              ) : (
                <div className="flex h-full w-full items-center justify-center text-slate-400">
                  <UserCircle className="h-8 w-8" />
                </div>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <label className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-slate-200 px-3 py-1.5 text-[11px] font-semibold text-slate-600 hover:bg-slate-50">
                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) {
                      void handlePhotoUpload(file);
                    }
                    event.currentTarget.value = '';
                  }}
                />
                <UploadCloud className="h-3.5 w-3.5" />
                {photoUploading ? t('Uploading...') : t('Upload photo')}
              </label>
              {draft.profile.photoKey && (
                <button
                  type="button"
                  onClick={handlePhotoClear}
                  className="text-[11px] font-semibold text-slate-500 hover:text-slate-700"
                >
                  {t('Remove photo')}
                </button>
              )}
              {photoError && <div className="text-xs text-red-600">{photoError}</div>}
            </div>
          </div>
          <label className="text-xs text-slate-500 dark:text-slate-400">
            {t('Display name')}
            <input
              value={draft.profile.displayName}
              onChange={(event) => updateProfile('displayName', event.target.value)}
              className={inputClass}
            />
          </label>
          <label className="text-xs text-slate-500 dark:text-slate-400">
            {t('Email')}
            <input
              value={authUser?.email || t('Not connected')}
              readOnly
              disabled
              className={inputClass}
            />
          </label>
          <label className="text-xs text-slate-500 dark:text-slate-400">
            {t('Language')}
            <select
              value={draft.profile.language}
              onChange={(event) => updateProfile('language', event.target.value as Language)}
              className={inputClass}
            >
              <option value="en">{t('English')}</option>
              <option value="zh">{t('Chinese')}</option>
            </select>
          </label>
          <label className="text-xs text-slate-500 dark:text-slate-400">
            {t('Timezone')}
            <select
              value={draft.preferences.timezone}
              onChange={(event) => updatePreferences('timezone', event.target.value)}
              className={inputClass}
            >
              {timezoneOptions.map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </select>
          </label>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {t(
              'Defaulted to your device timezone. Change it if your memories should follow a different location.'
            )}
          </p>
        </SectionCard>

        <SectionCard
          title={t('Appearance')}
          description={t('Tune the interface to match your preferences.')}
          icon={<Sliders size={18} />}
          action={
            <span className="rounded-full bg-slate-100 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:bg-slate-800 dark:text-slate-300">
              {t('Coming soon')}
            </span>
          }
        >
          <div className="rounded-lg border border-dashed border-slate-200 bg-white/70 px-4 py-3 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-950/40 dark:text-slate-400">
            {t('Theme is managed from the sidebar toggle for now.')}
            <div className="mt-2 text-[11px] text-slate-400 dark:text-slate-500">
              {t('Font size scaling is coming soon.')}
            </div>
          </div>
          <label className="text-xs text-slate-500 dark:text-slate-400">
            {t('Font size')}
            <select
              value={draft.appearance.fontScale}
              onChange={(event) => updateAppearance('fontScale', event.target.value as FontScale)}
              className={inputDisabledClass}
              disabled
            >
              <option value="sm">{t('Small')}</option>
              <option value="md">{t('Default')}</option>
              <option value="lg">{t('Large')}</option>
            </select>
          </label>
          <ToggleRow
            label={t('Reduce motion')}
            description={t('Minimize animations for a calmer experience.')}
            checked={draft.appearance.reduceMotion}
            onChange={(value) => updateAppearance('reduceMotion', value)}
          />
        </SectionCard>

        <SectionCard
          title={t('Timeline')}
          description={t('Default behaviors for how your memories appear.')}
          icon={<Calendar size={18} />}
        >
          <label className="text-xs text-slate-500 dark:text-slate-400">
            {t('Default view')}
            <select
              value={draft.timeline.defaultView}
              onChange={(event) => updateTimeline('defaultView', event.target.value as DefaultView)}
              className={inputClass}
            >
              <option value="day">{t('Day')}</option>
              <option value="week">{t('Week')}</option>
              <option value="month">{t('Month')}</option>
              <option value="year">{t('Year')}</option>
              <option value="all">{t('All')}</option>
            </select>
          </label>
          <ToggleRow
            label={t('Show captions')}
            description={t('Display generated captions in timeline cards.')}
            checked={draft.timeline.showCaptions}
            onChange={(value) => updateTimeline('showCaptions', value)}
          />
          <ToggleRow
            label={t('Show highlights')}
            description={t('Surface daily highlight thumbnails in week/month views.')}
            checked={draft.timeline.showHighlights}
            onChange={(value) => updateTimeline('showHighlights', value)}
          />
          <ToggleRow
            label={t('Show episodes')}
            description={t('Group moments into episodes in the day view.')}
            checked={draft.timeline.showEpisodes}
            onChange={(value) => updateTimeline('showEpisodes', value)}
          />
        </SectionCard>

        <SectionCard
          title={t('Ingest')}
          description={t('Defaults for new uploads and provider connections.')}
          icon={<Cloud size={18} />}
          action={
            <span className="rounded-full bg-slate-100 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:bg-slate-800 dark:text-slate-300">
              {t('Coming soon')}
            </span>
          }
        >
          <label className="text-xs text-slate-500 dark:text-slate-400">
            {t('Default provider')}
            <select
              value={draft.ingest.defaultProvider}
              onChange={(event) => updateIngest('defaultProvider', event.target.value as Provider)}
              className={inputDisabledClass}
              disabled
            >
              <option value="local">{t('Local upload')}</option>
              <option value="google_photos">{t('Google Photos')}</option>
            </select>
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <ToggleRow
              label={t('Auto dedupe')}
              description={t('Skip duplicates during ingest.')}
              checked={draft.ingest.autoDedupe}
              onChange={(value) => updateIngest('autoDedupe', value)}
              disabled
            />
            <ToggleRow
              label={t('Auto episodes')}
              description={t('Merge moments into episodes on ingest.')}
              checked={draft.ingest.autoEpisodes}
              onChange={(value) => updateIngest('autoEpisodes', value)}
              disabled
            />
          </div>
          <label className="text-xs text-slate-500 dark:text-slate-400">
            {t('Batch limit')}
            <input
              type="number"
              min={1}
              value={draft.ingest.batchLimit}
              onChange={(event) =>
                updateIngest('batchLimit', Math.max(1, Number(event.target.value) || 1))
              }
              className={inputDisabledClass}
              disabled
            />
          </label>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {t('These settings will apply in a future update.')}
          </p>
          <div className="rounded-xl border border-slate-100 bg-white/70 p-4 shadow-sm dark:border-slate-800 dark:bg-slate-950/40">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  {t('Integrations')}
                </p>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  {t('Manage connected services for automated ingest.')}
                </p>
              </div>
            </div>
            <div className="mt-3 rounded-lg border border-slate-100 bg-white/80 p-4 shadow-sm dark:border-slate-800 dark:bg-slate-950/40">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                    {t('Google Photos')}
                  </p>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    {googleLoading
                      ? t('Checking connection...')
                      : googleStatus?.connected
                      ? t('Connected • {time}', {
                          time: formatLocalTime(googleStatus.connected_at),
                        })
                      : t('Not connected')}
                  </p>
                  {googleError && (
                    <p className="mt-1 text-xs text-red-600">
                      {t('Status error: {message}', { message: googleError })}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded-full px-3 py-1 text-[11px] font-semibold ${
                      googleStatus?.connected
                        ? 'bg-emerald-100 text-emerald-700'
                        : 'bg-slate-100 text-slate-600'
                    }`}
                  >
                    {googleStatus?.connected ? t('Connected') : t('Disconnected')}
                  </span>
                  <div className="flex items-center gap-2">
                    {googleStatus?.connected ? (
                      <button
                        type="button"
                        onClick={handleDisconnect}
                        className="rounded-full border border-slate-200 px-3 py-1 text-[11px] font-semibold text-slate-600 hover:bg-slate-50"
                      >
                        {t('Disconnect')}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={handleConnect}
                        className="rounded-full bg-slate-900 px-3 py-1 text-[11px] font-semibold text-white hover:bg-slate-800"
                      >
                        {t('Connect')}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </SectionCard>

        <SectionCard
          title={t('API Keys')}
          description={t('Generate tokens for OpenClaw or other integrations.')}
          icon={<KeyRound size={18} />}
        >
          <div className="rounded-xl border border-slate-100 bg-white/70 p-4 text-xs text-slate-500 shadow-sm dark:border-slate-800 dark:bg-slate-950/40 dark:text-slate-400">
            {t('Keys are shown only once. Store them securely.')}
          </div>

          <div className="grid gap-3 sm:grid-cols-[1.2fr_0.8fr_auto]">
            <label className="text-xs text-slate-500 dark:text-slate-400">
              {t('Key name')}
              <input
                value={createKeyName}
                onChange={(event) => setCreateKeyName(event.target.value)}
                className={inputClass}
              />
            </label>
            <label className="text-xs text-slate-500 dark:text-slate-400">
              {t('Expires')}
              <select
                value={createKeyExpiry}
                onChange={(event) => setCreateKeyExpiry(event.target.value)}
                className={inputClass}
              >
                <option value="0">{t('Never')}</option>
                <option value="30">{t('30 days')}</option>
                <option value="90">{t('90 days')}</option>
                <option value="365">{t('1 year')}</option>
              </select>
            </label>
            <div className="flex items-end">
              <button
                type="button"
                onClick={handleCreateKey}
                className="h-10 rounded-full bg-slate-900 px-4 text-xs font-semibold text-white hover:bg-slate-800"
                disabled={creatingKey}
              >
                {creatingKey ? t('Creating...') : t('Create key')}
              </button>
            </div>
          </div>

          {createdKey && (
            <div className="rounded-xl border border-emerald-100 bg-emerald-50/60 p-4 text-xs text-emerald-800 shadow-sm dark:border-emerald-900/40 dark:bg-emerald-950/40 dark:text-emerald-200">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold">{t('New key created')}</p>
                  <p className="mt-1 text-xs text-emerald-700 dark:text-emerald-300">
                    {t('Copy it now — it will not be shown again.')}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => copyToClipboard(createdKey.key, t('API key'))}
                    className="rounded-full bg-emerald-600 px-3 py-1 text-[11px] font-semibold text-white hover:bg-emerald-500"
                  >
                    {t('Copy key')}
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      copyToClipboard(
                        `export OMNIMEMORY_API_URL=\"${apiBase}\"\nexport OMNIMEMORY_API_TOKEN=\"${createdKey.key}\"`,
                        t('OpenClaw env')
                      )
                    }
                    className="rounded-full border border-emerald-300 px-3 py-1 text-[11px] font-semibold text-emerald-700 hover:bg-emerald-100 dark:border-emerald-700 dark:text-emerald-200"
                  >
                    {t('Copy OpenClaw env')}
                  </button>
                </div>
              </div>
              <div className="mt-3 break-all rounded-lg border border-emerald-100 bg-white/70 p-3 font-mono text-[11px] text-emerald-900 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-100">
                {createdKey.key}
              </div>
            </div>
          )}

          <div className="rounded-xl border border-slate-100 bg-white/80 p-4 shadow-sm dark:border-slate-800 dark:bg-slate-950/40">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  {t('Active keys')}
                </p>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  {apiKeysLoading
                    ? t('Loading keys...')
                    : apiKeys.length === 0
                    ? t('No keys created yet.')
                    : t('{count} active keys', { count: apiKeys.length })}
                </p>
                {apiKeysError && (
                  <p className="mt-1 text-xs text-red-600">
                    {t('Key error: {message}', { message: apiKeysError })}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={fetchApiKeys}
                className="rounded-full border border-slate-200 px-3 py-1 text-[11px] font-semibold text-slate-600 hover:bg-slate-50"
                disabled={apiKeysLoading}
              >
                {t('Refresh')}
              </button>
            </div>
            <div className="mt-3 space-y-2">
              {apiKeys.map((key) => (
                <div
                  key={key.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-100 bg-white/90 px-4 py-3 text-xs text-slate-600 shadow-sm dark:border-slate-800 dark:bg-slate-950/40 dark:text-slate-300"
                >
                  <div>
                    <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                      {key.name}
                    </p>
                    <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                      {t('Prefix')} {key.key_prefix} · {t('Created')} {formatLocalTime(key.created_at)}
                      {key.last_used_at
                        ? ` · ${t('Last used')} ${formatLocalTime(key.last_used_at)}`
                        : ''}
                      {key.expires_at ? ` · ${t('Expires')} ${formatLocalTime(key.expires_at)}` : ''}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        copyToClipboard(key.key_prefix, t('Prefix'))
                      }
                      className="rounded-full border border-slate-200 px-3 py-1 text-[11px] font-semibold text-slate-600 hover:bg-slate-50"
                    >
                      {t('Copy prefix')}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleRevokeKey(key)}
                      className="rounded-full border border-rose-200 px-3 py-1 text-[11px] font-semibold text-rose-600 hover:bg-rose-50"
                    >
                      {t('Revoke')}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </SectionCard>

        <SectionCard
          title={t('OpenClaw')}
          description={t('Connect OmniMemory with your OpenClaw AI assistant.')}
          icon={<Cpu size={18} />}
        >
          <div className="rounded-xl border border-slate-100 bg-white/70 p-4 shadow-sm dark:border-slate-800 dark:bg-slate-950/40">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  {t('Memory sync')}
                </p>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  {t('Sync daily summaries to OpenClaw memory files (~/.openclaw/workspace/memory/).')}
                </p>
              </div>
            </div>
          </div>
          <ToggleRow
            label={t('Sync daily summaries')}
            description={t('Write daily summaries and episodes to OpenClaw memory for reference.')}
            checked={draft.openclaw.syncMemory}
            onChange={(value) => updateOpenClaw('syncMemory', value)}
          />
          <label className="text-xs text-slate-500 dark:text-slate-400">
            {t('OpenClaw workspace')}
            <input
              value={draft.openclaw.workspace}
              onChange={(event) => updateOpenClaw('workspace', event.target.value)}
              className={inputClass}
              placeholder="~/.openclaw"
            />
          </label>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {t('Create an API key above to connect OpenClaw to OmniMemory.')}
          </p>
        </SectionCard>

        <SectionCard
          title={t('Notifications')}
          description={t('Control alerts for syncs and daily highlights.')}
          icon={<Bell size={18} />}
        >
          <ToggleRow
            label={t('Weekly recap')}
            description={t('Receive a summary every Sunday.')}
            checked={draft.notifications.weeklySummary}
            onChange={updateNotifications}
          />
        </SectionCard>

        <SectionCard
          title={t('Usage')}
          description={t('Track storage and AI usage for your account.')}
          icon={<Cloud size={18} />}
        >
          <div className="grid gap-3 sm:grid-cols-3">
            {usageSummary.map((item) => (
              <div
                key={item.label}
                className="rounded-lg border border-slate-100 bg-white/80 px-4 py-3 text-sm shadow-sm dark:border-slate-800 dark:bg-slate-950/40"
              >
                <p className="text-xs text-slate-500 dark:text-slate-400">{item.label}</p>
                <p className="mt-1 text-lg font-semibold text-slate-900 dark:text-slate-100">
                  {item.value}
                </p>
              </div>
            ))}
          </div>
          {usageError ? (
            <p className="text-xs text-rose-500">
              {t('Usage metrics unavailable: {message}', { message: usageError })}
            </p>
          ) : (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {t('Usage metrics refresh from the dashboard stats API.')}
            </p>
          )}
        </SectionCard>

        <SectionCard
          title={t('Privacy & Data')}
          description={t('Control how your data is stored and shared.')}
          icon={<Lock size={18} />}
          action={
            <span className="rounded-full bg-slate-100 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:bg-slate-800 dark:text-slate-300">
              {t('Coming soon')}
            </span>
          }
          className="xl:col-span-2"
        >
          <ToggleRow
            label={t('Personalization')}
            description={t('Allow summaries to use your historical preferences.')}
            checked={draft.privacy.allowPersonalization}
            onChange={(value) => updatePrivacy('allowPersonalization', value)}
            disabled
          />
          <ToggleRow
            label={t('Share anonymous usage')}
            description={t('Help improve OmniMemory with anonymized metrics.')}
            checked={draft.privacy.shareUsage}
            onChange={(value) => updatePrivacy('shareUsage', value)}
            disabled
          />
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {t('These settings will apply in a future update.')}
          </p>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              disabled
              className="rounded-full border border-slate-200 px-4 py-2 text-xs text-slate-400"
            >
              {t('Export data (coming soon)')}
            </button>
            <button
              type="button"
              disabled
              className="rounded-full border border-red-200 px-4 py-2 text-xs text-red-300"
            >
              {t('Delete account (coming soon)')}
            </button>
          </div>
        </SectionCard>

        <SectionCard
          title={t('Advanced')}
          description={t('Optional flags for early access features.')}
          icon={<Sliders size={18} />}
          action={
            <span className="rounded-full bg-slate-100 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:bg-slate-800 dark:text-slate-300">
              {t('Coming soon')}
            </span>
          }
          className="xl:col-span-2"
        >
          <ToggleRow
            label={t('Experimental features')}
            description={t('Try early UI experiments in the timeline.')}
            checked={draft.advanced.experimentalFeatures}
            onChange={(value) => updateAdvanced('experimentalFeatures', value)}
            disabled
          />
          <ToggleRow
            label={t('Debug telemetry')}
            description={t('Enable additional logs for troubleshooting.')}
            checked={draft.advanced.debugTelemetry}
            onChange={(value) => updateAdvanced('debugTelemetry', value)}
            disabled
          />
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {t('These settings will apply in a future update.')}
          </p>
        </SectionCard>
      </div>
    </PageMotion>
  );
};
