import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { HardDrive, Image as ImageIcon, Link as LinkIcon, Activity, Calendar, Mic, Play, Video, Cpu, DollarSign } from 'lucide-react';
import { apiGet } from '../services/api';
import { PageMotion } from './PageMotion';
import { DashboardRecentItem, DashboardStatsResponse, TimelineFocus } from '../types';
import { useSettings } from '../contexts/SettingsContext';
import { useI18n } from '../i18n/useI18n';
import { addDaysZoned, dateKeyToDate, formatDateKey, toZonedDate } from '../utils/time';

const StatCard = ({ title, value, icon, subtext }: { title: string, value: string | number, icon: React.ReactNode, subtext?: string }) => (
  <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-100 flex items-start space-x-4 hover:shadow-md transition-shadow">
    <div className="p-3 bg-primary-50 text-primary-600 rounded-lg">
      {icon}
    </div>
    <div>
      <p className="text-sm font-medium text-slate-500">{title}</p>
      <h3 className="text-2xl font-bold text-slate-900 mt-1">{value}</h3>
      {subtext && <p className="text-xs text-slate-400 mt-1">{subtext}</p>}
    </div>
  </div>
);

const formatNumber = (value: number, locale: string) => new Intl.NumberFormat(locale).format(value);

const formatCurrency = (value: number, locale: string) =>
  new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 4,
  }).format(value);

const RecentActivityItem: React.FC<{
  item: DashboardRecentItem;
  onSelect?: (item: DashboardRecentItem) => void;
  formatDateLabel: (value: string | undefined) => string;
  buildLabel: (item: DashboardRecentItem) => string;
  processedLabel: string;
  processingLabel: string;
}> = ({ item, onSelect, formatDateLabel, buildLabel, processedLabel, processingLabel }) => {
  const isVideo = item.item_type === 'video';
  const isAudio = item.item_type === 'audio';
  const thumbnailUrl = isVideo ? item.poster_url : item.download_url;
  const [imageFailed, setImageFailed] = useState(false);
  const hasThumbnail = thumbnailUrl && !imageFailed;
  return (
    <button
      type="button"
      onClick={() => onSelect?.(item)}
      className="flex w-full items-center space-x-4 rounded-lg p-3 text-left transition-colors hover:bg-slate-50 group"
    >
      {hasThumbnail ? (
        <div className="relative w-12 h-12">
          <img
            src={thumbnailUrl}
            alt={buildLabel(item)}
            className="w-12 h-12 rounded-lg object-cover shadow-sm group-hover:scale-105 transition-transform"
            onError={() => setImageFailed(true)}
            loading="lazy"
          />
          {isVideo && (
            <span className="absolute inset-0 flex items-center justify-center text-white">
              <span className="flex items-center justify-center w-6 h-6 rounded-full bg-black/60">
                <Play className="w-3 h-3" />
              </span>
            </span>
          )}
        </div>
      ) : (
        <div className="w-12 h-12 rounded-lg bg-slate-100 flex items-center justify-center text-slate-400">
          {isVideo ? <Video className="w-5 h-5" /> : isAudio ? <Mic className="w-5 h-5" /> : <ImageIcon className="w-5 h-5" />}
        </div>
      )}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-slate-900 truncate">{buildLabel(item)}</p>
        <div className="flex items-center text-xs text-slate-500 mt-0.5">
          <Calendar className="w-3 h-3 mr-1" />
          <span>{formatDateLabel(item.captured_at)}</span>
        </div>
      </div>
      <div
        className={`text-xs font-medium px-2 py-1 rounded-full ${
          item.processed ? 'text-green-600 bg-green-50' : 'text-slate-600 bg-slate-100'
        }`}
      >
        {item.processed ? processedLabel : processingLabel}
      </div>
    </button>
  );
};

interface DashboardProps {
  onOpenTimeline?: (focus: TimelineFocus) => void;
}

export const Dashboard: React.FC<DashboardProps> = ({ onOpenTimeline }) => {
  const { settings } = useSettings();
  const { t, locale } = useI18n();
  const timeZone = settings.preferences.timezone;
  const [stats, setStats] = useState<DashboardStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const itemTypeLabels = useMemo(
    () => ({
      photo: t('Photo'),
      video: t('Video'),
      audio: t('Audio'),
      document: t('Document'),
    }),
    [t]
  );
  const formatDateLabel = useCallback(
    (value?: string) => {
      if (!value) return t('Unknown date');
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) {
        return t('Unknown date');
      }
      return new Intl.DateTimeFormat(locale, {
        timeZone,
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      }).format(parsed);
    },
    [locale, t, timeZone]
  );
  const buildLabel = useCallback(
    (item: DashboardRecentItem) =>
      item.caption ||
      item.original_filename ||
      t('{type} upload', { type: itemTypeLabels[item.item_type] ?? item.item_type }),
    [itemTypeLabels, t]
  );
  const defaultRange = useMemo(() => {
    const today = toZonedDate(new Date(), timeZone);
    const start = addDaysZoned(today, -6, timeZone);
    return {
      start: formatDateKey(start, timeZone),
      end: formatDateKey(today, timeZone),
    };
  }, [timeZone]);
  const [rangeStart, setRangeStart] = useState(defaultRange.start);
  const [rangeEnd, setRangeEnd] = useState(defaultRange.end);

  useEffect(() => {
    setRangeStart(defaultRange.start);
    setRangeEnd(defaultRange.end);
  }, [defaultRange.end, defaultRange.start]);

  const normalizedRange = useMemo(() => {
    if (!rangeStart || !rangeEnd) return null;
    const start = rangeStart <= rangeEnd ? rangeStart : rangeEnd;
    const end = rangeStart <= rangeEnd ? rangeEnd : rangeStart;
    return { start, end };
  }, [rangeStart, rangeEnd]);

  const rangeDays = useMemo(() => {
    if (!normalizedRange) return 0;
    const start = dateKeyToDate(normalizedRange.start, timeZone);
    const end = dateKeyToDate(normalizedRange.end, timeZone);
    if (!start || !end) {
      return 0;
    }
    const diffMs = end.getTime() - start.getTime();
    return Math.max(1, Math.floor(diffMs / (24 * 60 * 60 * 1000)) + 1);
  }, [normalizedRange, timeZone]);

  const formatRangeLabel = (value: string) => {
    const date = dateKeyToDate(value, timeZone);
    if (!date) {
      return value;
    }
    if (rangeDays > 14) {
      return new Intl.DateTimeFormat(locale, { timeZone, month: 'short', day: 'numeric' }).format(date);
    }
    return new Intl.DateTimeFormat(locale, { timeZone, weekday: 'short' }).format(date);
  };

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const query = new URLSearchParams();
        if (normalizedRange?.start) query.set('start_date', normalizedRange.start);
        if (normalizedRange?.end) query.set('end_date', normalizedRange.end);
        const path = query.toString() ? `/dashboard/stats?${query.toString()}` : '/dashboard/stats';
        const data = await apiGet<DashboardStatsResponse>(path);
        if (mounted) {
          setStats(data);
        }
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : t('Failed to load dashboard.'));
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, [normalizedRange]);

  const storageUsedGB = useMemo(() => {
    if (!stats?.storage_used_bytes) return 0;
    return stats.storage_used_bytes / (1024 * 1024 * 1024);
  }, [stats?.storage_used_bytes]);

  const activityData = useMemo(
    () =>
      (stats?.activity || []).map(point => ({
        name: formatRangeLabel(point.date),
        count: point.count,
      })),
    [stats?.activity, rangeDays, locale, timeZone]
  );

  const usageData = useMemo(
    () =>
      (stats?.usage_daily || []).map(point => ({
        name: formatRangeLabel(point.date),
        tokens: point.total_tokens,
        cost: point.cost_usd,
      })),
    [stats?.usage_daily, rangeDays, locale, timeZone]
  );

  const usageWeek = stats?.usage_this_week;
  const usageAllTime = stats?.usage_all_time;

  return (
    <PageMotion className="h-full overflow-y-auto p-8 space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">{t('Dashboard')}</h1>
        <p className="text-slate-500 mt-1">{t('Overview of your digital life log.')}</p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg">
          {error}
        </div>
      )}

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard 
          title={t('Total Memories')}
          value={stats ? formatNumber(stats.total_items, locale) : '—'}
          icon={<ImageIcon size={24} />} 
          subtext={
            stats ? t('{count} processed', { count: formatNumber(stats.processed_items, locale) }) : undefined
          }
        />
        <StatCard 
          title={t('Storage Used')}
          value={stats ? t('{value} GB', { value: storageUsedGB.toFixed(2) }) : '—'}
          icon={<HardDrive size={24} />} 
          subtext={
            stats ? t('{count} failed items', { count: formatNumber(stats.failed_items, locale) }) : undefined
          }
        />
        <StatCard 
          title={t('Weekly Uploads')}
          value={stats ? stats.uploads_last_7_days : '—'} 
          icon={<Activity size={24} />} 
          subtext={t('Last 7 days')}
        />
        <StatCard 
          title={t('Connected Sources')}
          value={stats ? stats.active_connections : '—'} 
          icon={<LinkIcon size={24} />} 
          subtext={t('Active connections')}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Activity Chart */}
        <div className="lg:col-span-2 bg-white p-6 rounded-xl shadow-sm border border-slate-100">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-6">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">{t('Ingestion Activity')}</h2>
              {normalizedRange && (
                <p className="text-xs text-slate-500">
                  {normalizedRange.start} → {normalizedRange.end}
                </p>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
              <label className="flex items-center gap-2">
                <span className="text-slate-500">{t('Start')}</span>
                <input
                  type="date"
                  value={rangeStart}
                  onChange={(event) => setRangeStart(event.target.value)}
                  className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600"
                />
              </label>
              <label className="flex items-center gap-2">
                <span className="text-slate-500">{t('End')}</span>
                <input
                  type="date"
                  value={rangeEnd}
                  onChange={(event) => setRangeEnd(event.target.value)}
                  className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600"
                />
              </label>
            </div>
          </div>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={activityData}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis 
                  dataKey="name" 
                  axisLine={false} 
                  tickLine={false} 
                  tick={{ fill: '#64748b', fontSize: 12 }} 
                  dy={10}
                />
                <YAxis 
                  axisLine={false} 
                  tickLine={false} 
                  tick={{ fill: '#64748b', fontSize: 12 }} 
                />
                <Tooltip 
                  cursor={{ fill: '#f8fafc' }}
                  contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                />
                <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]} barSize={32} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Recent Items */}
        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-100">
          <h2 className="text-lg font-semibold text-slate-900 mb-4">{t('Recent Memories')}</h2>
          <div className="space-y-2">
            {stats?.recent_items?.length ? (
              stats.recent_items.map(item => (
                <RecentActivityItem
                  key={item.id}
                  item={item}
                  onSelect={(selected) => {
                    onOpenTimeline?.({
                      viewMode: 'day',
                      anchorDate: selected.captured_at || new Date().toISOString(),
                      itemId: selected.id,
                    });
                  }}
                  formatDateLabel={formatDateLabel}
                  buildLabel={buildLabel}
                  processedLabel={t('Processed')}
                  processingLabel={t('Processing')}
                />
              ))
            ) : (
              <div className="text-sm text-slate-500 py-4 text-center">
                {loading ? t('Loading recent memories…') : t('No recent memories yet.')}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={() => onOpenTimeline?.({ viewMode: 'all' })}
            className="w-full mt-4 py-2 text-sm text-primary-600 font-medium hover:bg-primary-50 rounded-lg transition-colors"
          >
            {t('View All Memories')}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 bg-white p-6 rounded-xl shadow-sm border border-slate-100">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between mb-6">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">{t('AI Usage')}</h2>
              {normalizedRange && (
                <p className="text-xs text-slate-500">
                  {normalizedRange.start} → {normalizedRange.end}
                </p>
              )}
            </div>
            <div className="text-xs text-slate-400">{t('Estimated tokens + cost')}</div>
          </div>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={usageData}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis
                  dataKey="name"
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: '#64748b', fontSize: 12 }}
                  dy={10}
                />
                <YAxis
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: '#64748b', fontSize: 12 }}
                />
                <Tooltip
                  cursor={{ fill: '#f8fafc' }}
                  contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                  formatter={(value, name) => {
                    if (name === 'tokens') {
                      return [
                        t('{count} tokens', { count: formatNumber(value as number, locale) }),
                        t('Tokens'),
                      ];
                    }
                    return [value, name];
                  }}
                  labelFormatter={(label) => t('Day: {label}', { label })}
                  content={({ label, payload }) => {
                    if (!payload || payload.length === 0) return null;
                    const point = payload[0].payload as { tokens: number; cost: number };
                    return (
                      <div className="rounded-lg bg-white p-3 text-xs text-slate-600 shadow-lg">
                        <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
                        <div className="mt-1 font-semibold text-slate-900">
                          {t('{count} tokens', { count: formatNumber(point.tokens, locale) })}
                        </div>
                        <div className="mt-1 text-slate-500">
                          {t('Cost: {amount}', { amount: formatCurrency(point.cost, locale) })}
                        </div>
                      </div>
                    );
                  }}
                />
                <Bar dataKey="tokens" fill="#0ea5e9" radius={[4, 4, 0, 0]} barSize={32} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-100 space-y-4">
          <h2 className="text-lg font-semibold text-slate-900">{t('Usage Totals')}</h2>
          <div className="space-y-3">
            <div className="flex items-start gap-3 rounded-lg border border-slate-100 p-3">
              <div className="rounded-lg bg-slate-50 p-2 text-slate-600">
                <Cpu className="h-4 w-4" />
              </div>
              <div>
                <p className="text-xs text-slate-500">{t('This week tokens')}</p>
                <p className="text-sm font-semibold text-slate-900">
                  {usageWeek ? formatNumber(usageWeek.total_tokens, locale) : '—'}
                </p>
                <p className="text-[11px] text-slate-400">
                  {t('Prompt {prompt} / Output {output}', {
                    prompt: usageWeek ? formatNumber(usageWeek.prompt_tokens, locale) : '—',
                    output: usageWeek ? formatNumber(usageWeek.output_tokens, locale) : '—',
                  })}
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3 rounded-lg border border-slate-100 p-3">
              <div className="rounded-lg bg-slate-50 p-2 text-slate-600">
                <DollarSign className="h-4 w-4" />
              </div>
              <div>
                <p className="text-xs text-slate-500">{t('This week cost')}</p>
                <p className="text-sm font-semibold text-slate-900">
                  {usageWeek ? formatCurrency(usageWeek.cost_usd, locale) : '—'}
                </p>
              </div>
            </div>
            <div className="border-t border-slate-100 pt-3 text-xs text-slate-500">
              <div className="flex items-center justify-between">
                <span>{t('All time tokens')}</span>
                <span className="font-semibold text-slate-700">
                  {usageAllTime ? formatNumber(usageAllTime.total_tokens, locale) : '—'}
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between">
                <span>{t('All time cost')}</span>
                <span className="font-semibold text-slate-700">
                  {usageAllTime ? formatCurrency(usageAllTime.cost_usd, locale) : '—'}
                </span>
              </div>
            </div>
          </div>
          <p className="text-[11px] text-slate-400">
            {t('Cost uses static Gemini pricing and may differ from billing.')}
          </p>
        </div>
      </div>
    </PageMotion>
  );
};
