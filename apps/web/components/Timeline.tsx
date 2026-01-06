import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Calendar, ChevronLeft, ChevronRight, FileText, Image as ImageIcon, Mic, Play, Search, Star, UploadCloud, Video, X } from 'lucide-react';
import { apiDelete, apiGet, apiPatch, apiPost, apiPostForm } from '../services/api';
import {
  IngestResponse,
  SearchResponse,
  SearchResult,
  TimelineDay,
  TimelineDailySummary,
  TimelineEpisode,
  TimelineEpisodeDetail,
  TimelineItem,
  TimelineItemDetail,
  TimelineItemsResponse,
  TimelineFocus,
  TimelineViewMode,
  UploadUrlResponse,
} from '../types';
import { PageMotion } from './PageMotion';
import { useSettings } from '../contexts/SettingsContext';
import { translateFromStorage } from '../i18n/core';
import { useI18n } from '../i18n/useI18n';
import {
  addDaysZoned,
  buildZonedDate,
  dateKeyToDate,
  formatDateKey,
  getDateParts,
  getTimeZoneOffsetMinutes,
  parseDateKey,
  toZonedDate,
} from '../utils/time';

type ViewMode = TimelineViewMode;
type EpisodeCard = TimelineEpisode & {
  isSynthetic?: boolean;
  syntheticItemId?: string;
};

const formatDate = (value: string | Date | undefined, locale: string, timeZone: string) => {
  if (!value) return translateFromStorage('Unknown date');
  const parsed = typeof value === 'string' ? new Date(value) : value;
  if (Number.isNaN(parsed.getTime())) {
    return translateFromStorage('Unknown date');
  }
  return new Intl.DateTimeFormat(locale, {
    timeZone,
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(parsed);
};

const formatMonthLabel = (value: Date, locale: string, timeZone: string) =>
  new Intl.DateTimeFormat(locale, { timeZone, month: 'long' }).format(value);

const formatDayLabel = (value: Date, locale: string, timeZone: string) =>
  new Intl.DateTimeFormat(locale, { timeZone, weekday: 'short' }).format(value);

const buildLabel = (item: TimelineItem) =>
  item.caption ||
  item.original_filename ||
  translateFromStorage('{type} upload', { type: translateFromStorage(item.item_type) });

const formatTime = (value: string | Date | undefined, locale: string, timeZone: string) => {
  if (!value) return translateFromStorage('Unknown time');
  const parsed = typeof value === 'string' ? new Date(value) : value;
  if (Number.isNaN(parsed.getTime())) {
    return translateFromStorage('Unknown time');
  }
  return new Intl.DateTimeFormat(locale, {
    timeZone,
    hour: 'numeric',
    minute: '2-digit',
  }).format(parsed);
};

const formatTimeRange = (
  start: string | null | undefined,
  end: string | null | undefined,
  locale: string,
  timeZone: string
) => {
  if (!start || !end) return translateFromStorage('Time window unknown');
  return `${formatTime(start, locale, timeZone)} - ${formatTime(end, locale, timeZone)}`;
};

const formatDuration = (ms: number) => {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
};

const formatClockTime = (value: Date, locale: string, timeZone: string) =>
  new Intl.DateTimeFormat(locale, {
    timeZone,
    hour: 'numeric',
    minute: '2-digit',
  }).format(value);

const buildDateWithTime = (value: Date, timeValue: string, timeZone: string) => {
  const [hours, minutes] = timeValue.split(':').map((part) => Number(part));
  const safeHours = Number.isFinite(hours) ? hours : 0;
  const safeMinutes = Number.isFinite(minutes) ? minutes : 0;
  const { year, month, day } = getDateParts(value, timeZone);
  return buildZonedDate(year, month, day, timeZone, safeHours, safeMinutes);
};

const inferItemType = (file: File) => {
  if (file.type.startsWith('image/')) return 'photo';
  if (file.type.startsWith('video/')) return 'video';
  if (file.type.startsWith('audio/')) return 'audio';
  return 'document';
};

const getMediaDuration = (file: File): Promise<number | null> => {
  if (!file.type.startsWith('video/') && !file.type.startsWith('audio/')) {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const media = document.createElement(file.type.startsWith('video/') ? 'video' : 'audio');
    const cleanup = () => {
      URL.revokeObjectURL(url);
    };
    media.preload = 'metadata';
    media.onloadedmetadata = () => {
      const duration = Number.isFinite(media.duration) ? media.duration : null;
      cleanup();
      resolve(duration);
    };
    media.onerror = () => {
      cleanup();
      resolve(null);
    };
    media.src = url;
  });
};

const addMonthsZoned = (value: Date, months: number, timeZone: string) => {
  const { year, month, day } = getDateParts(value, timeZone);
  return buildZonedDate(year, month + months, day, timeZone);
};

const addYearsZoned = (value: Date, years: number, timeZone: string) => {
  const { year, month, day } = getDateParts(value, timeZone);
  return buildZonedDate(year + years, month, day, timeZone);
};

const startOfWeek = (value: Date, timeZone: string) => {
  const { year, month, day } = getDateParts(value, timeZone);
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  const offset = (weekday + 6) % 7;
  return addDaysZoned(value, -offset, timeZone);
};

const endOfWeek = (value: Date, timeZone: string) =>
  addDaysZoned(startOfWeek(value, timeZone), 6, timeZone);

const startOfMonth = (value: Date, timeZone: string) => {
  const { year, month } = getDateParts(value, timeZone);
  return buildZonedDate(year, month, 1, timeZone);
};

const endOfMonth = (value: Date, timeZone: string) => {
  const { year, month } = getDateParts(value, timeZone);
  return buildZonedDate(year, month + 1, 0, timeZone);
};

const startOfYear = (value: Date, timeZone: string) => {
  const { year } = getDateParts(value, timeZone);
  return buildZonedDate(year, 1, 1, timeZone);
};

const endOfYear = (value: Date, timeZone: string) => {
  const { year } = getDateParts(value, timeZone);
  return buildZonedDate(year, 12, 31, timeZone);
};

const formatRangeLabel = (view: ViewMode, anchor: Date, locale: string, timeZone: string) => {
  if (view === 'day') {
    return new Intl.DateTimeFormat(locale, {
      timeZone,
      weekday: 'long',
      month: 'short',
      day: 'numeric',
    }).format(anchor);
  }
  if (view === 'week') {
    const start = startOfWeek(anchor, timeZone);
    const end = endOfWeek(anchor, timeZone);
    const formatter = new Intl.DateTimeFormat(locale, { timeZone, month: 'short', day: 'numeric' });
    return `${formatter.format(start)} - ${formatter.format(end)}`;
  }
  if (view === 'month') {
    return new Intl.DateTimeFormat(locale, { timeZone, month: 'long', year: 'numeric' }).format(anchor);
  }
  if (view === 'all') {
    return translateFromStorage('All time');
  }
  return getDateParts(anchor, timeZone).year.toString();
};

const buildDateRange = (start: Date, end: Date, timeZone: string) => {
  const dates: Date[] = [];
  let cursor = start;
  while (cursor <= end) {
    dates.push(cursor);
    cursor = addDaysZoned(cursor, 1, timeZone);
  }
  return dates;
};

const buildMonthGrid = (anchor: Date, timeZone: string) => {
  const gridStart = startOfWeek(startOfMonth(anchor, timeZone), timeZone);
  const gridEnd = endOfWeek(endOfMonth(anchor, timeZone), timeZone);
  return buildDateRange(gridStart, gridEnd, timeZone);
};

const isSameMonth = (value: Date, anchor: Date, timeZone: string) => {
  const left = getDateParts(value, timeZone);
  const right = getDateParts(anchor, timeZone);
  return left.year === right.year && left.month === right.month;
};

const getThumbnail = (item: TimelineItem) => {
  if (item.item_type === 'video') return item.poster_url || null;
  if (item.item_type === 'photo') return item.download_url || null;
  return null;
};

const getDayThumbnail = (day?: TimelineDay, preferHighlight = true) => {
  if (!day) return null;
  if (preferHighlight && day.highlight?.thumbnail_url) {
    return day.highlight.thumbnail_url;
  }
  const previewItem = day.items.find((item) => getThumbnail(item));
  return previewItem ? getThumbnail(previewItem) : null;
};

const VIEW_MODES: ViewMode[] = ['day', 'week', 'month', 'year', 'all'];

const EPISODES_PAGE_SIZE = 6;
const DAY_PAGE_SIZE = 20;
const ALL_PAGE_SIZE = 24;

interface TimelineProps {
  focus?: TimelineFocus | null;
  onFocusHandled?: () => void;
}

export const Timeline: React.FC<TimelineProps> = ({ focus, onFocusHandled }) => {
  const { settings } = useSettings();
  const { t, locale } = useI18n();
  const timelinePrefs = settings.timeline;
  const timeZone = settings.preferences.timezone;
  const [days, setDays] = useState<TimelineDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('day');
  const [viewInitialized, setViewInitialized] = useState(false);
  const [anchorDate, setAnchorDate] = useState<Date>(() => toZonedDate(new Date(), timeZone));
  const previousTimeZone = useRef(timeZone);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TimelineItemDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [episodeDetail, setEpisodeDetail] = useState<TimelineEpisodeDetail | null>(null);
  const [episodeLoading, setEpisodeLoading] = useState(false);
  const [episodeError, setEpisodeError] = useState<string | null>(null);
  const [episodeEditOpen, setEpisodeEditOpen] = useState(false);
  const [episodeEditTitle, setEpisodeEditTitle] = useState('');
  const [episodeEditSummary, setEpisodeEditSummary] = useState('');
  const [episodeEditSaving, setEpisodeEditSaving] = useState(false);
  const [episodeEditError, setEpisodeEditError] = useState<string | null>(null);
  const [episodeVisibleCount, setEpisodeVisibleCount] = useState(EPISODES_PAGE_SIZE);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [timeMode, setTimeMode] = useState<'file' | 'window'>('file');
  const [uploadStartTime, setUploadStartTime] = useState('09:00');
  const [uploadDurationHours, setUploadDurationHours] = useState('1');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const [uploadedCount, setUploadedCount] = useState(0);
  const [pendingUploadIds, setPendingUploadIds] = useState<string[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [pendingSelection, setPendingSelection] = useState<{ itemId?: string; episodeContextId?: string } | null>(null);

  const showHighlights = timelinePrefs.showHighlights;
  const showEpisodes = timelinePrefs.showEpisodes;
  const showCaptions = timelinePrefs.showCaptions;
  const [highlightSavingId, setHighlightSavingId] = useState<string | null>(null);
  const [highlightClearing, setHighlightClearing] = useState(false);
  const [highlightError, setHighlightError] = useState<string | null>(null);
  const [dailySummaryEditOpen, setDailySummaryEditOpen] = useState(false);
  const [dailySummaryTitle, setDailySummaryTitle] = useState('');
  const [dailySummaryText, setDailySummaryText] = useState('');
  const [dailySummarySaving, setDailySummarySaving] = useState(false);
  const [dailySummaryResetting, setDailySummaryResetting] = useState(false);
  const [dailySummaryError, setDailySummaryError] = useState<string | null>(null);
  const [dailySummaryVoiceLoading, setDailySummaryVoiceLoading] = useState(false);
  const [dailySummaryVoiceError, setDailySummaryVoiceError] = useState<string | null>(null);
  const [dayItems, setDayItems] = useState<TimelineItem[]>([]);
  const [dayOffset, setDayOffset] = useState(0);
  const [dayTotal, setDayTotal] = useState(0);
  const [dayLoading, setDayLoading] = useState(false);
  const [dayError, setDayError] = useState<string | null>(null);
  const [allItems, setAllItems] = useState<TimelineItem[]>([]);
  const [allOffset, setAllOffset] = useState(0);
  const [allTotal, setAllTotal] = useState(0);
  const [allLoading, setAllLoading] = useState(false);
  const [allError, setAllError] = useState<string | null>(null);
  const viewLabels = useMemo(
    () => ({
      day: t('Day'),
      week: t('Week'),
      month: t('Month'),
      year: t('Year'),
      all: t('All'),
    }),
    [t]
  );
  const itemTypeLabels = useMemo(
    () => ({
      photo: t('Photo'),
      video: t('Video'),
      audio: t('Audio'),
      document: t('Document'),
    }),
    [t]
  );
  const formatContextType = (value?: string | null) => {
    if (!value) return '';
    return t(value.replace(/_/g, ' '));
  };

  const range = useMemo(() => {
    if (viewMode === 'day') {
      return { start: anchorDate, end: anchorDate };
    }
    if (viewMode === 'all') {
      return { start: anchorDate, end: anchorDate };
    }
    if (viewMode === 'week') {
      return { start: startOfWeek(anchorDate, timeZone), end: endOfWeek(anchorDate, timeZone) };
    }
    if (viewMode === 'month') {
      return { start: startOfMonth(anchorDate, timeZone), end: endOfMonth(anchorDate, timeZone) };
    }
    return { start: startOfYear(anchorDate, timeZone), end: endOfYear(anchorDate, timeZone) };
  }, [viewMode, anchorDate, timeZone]);

  useEffect(() => {
    if (previousTimeZone.current === timeZone) {
      return;
    }
    setAnchorDate((prev) => {
      const previousZone = previousTimeZone.current;
      const dateKey = formatDateKey(prev, previousZone);
      const rebased = dateKeyToDate(dateKey, timeZone);
      return rebased ?? toZonedDate(new Date(), timeZone);
    });
    previousTimeZone.current = timeZone;
  }, [timeZone]);

  useEffect(() => {
    if (viewMode === 'all') {
      setLoading(false);
      return;
    }
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const tzOffsetMinutes = getTimeZoneOffsetMinutes(range.start, timeZone);
        const limit = viewMode === 'day' ? '1' : '600';
        const query = new URLSearchParams({
          start_date: formatDateKey(range.start, timeZone),
          end_date: formatDateKey(range.end, timeZone),
          limit,
          tz_offset_minutes: tzOffsetMinutes.toString(),
        });
        const data = await apiGet<TimelineDay[]>(`/timeline?${query.toString()}`);
        if (mounted) {
          setDays(data);
        }
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : t('Failed to load timeline.'));
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
  }, [range, reloadKey, viewMode, timeZone]);

  useEffect(() => {
    if (viewInitialized) {
      return;
    }
    if (focus?.viewMode) {
      setViewMode(focus.viewMode);
      setViewInitialized(true);
      return;
    }
    if (timelinePrefs.defaultView && viewMode !== timelinePrefs.defaultView) {
      setViewMode(timelinePrefs.defaultView);
    }
    setViewInitialized(true);
  }, [focus?.viewMode, timelinePrefs.defaultView, viewInitialized, viewMode]);

  useEffect(() => {
    if (!focus) {
      return;
    }
    if (focus.viewMode) {
      setViewMode(focus.viewMode);
      setViewInitialized(true);
    }
    if (focus.anchorDate) {
      const parsed = new Date(focus.anchorDate);
      if (!Number.isNaN(parsed.getTime())) {
        setAnchorDate(toZonedDate(parsed, timeZone));
      }
    }
    if (focus.itemId || focus.episodeContextId) {
      setSelectedEpisodeId(null);
      setSelectedItemId(null);
      setPendingSelection({ itemId: focus.itemId, episodeContextId: focus.episodeContextId });
    }
    if (focus.viewMode === 'all') {
      setSelectedEpisodeId(null);
      setSelectedItemId(null);
      setPendingSelection(null);
    }
    onFocusHandled?.();
  }, [focus, onFocusHandled, timeZone]);

  const dayLookup = useMemo(() => {
    const map = new Map<string, TimelineDay>();
    days.forEach((day) => {
      map.set(day.date, day);
    });
    return map;
  }, [days]);

  const dayKey = useMemo(() => formatDateKey(anchorDate, timeZone), [anchorDate, timeZone]);
  const dayEpisodes = useMemo(() => {
    if (viewMode !== 'day' || !showEpisodes) {
      return [];
    }
    return dayLookup.get(dayKey)?.episodes ?? [];
  }, [dayLookup, dayKey, showEpisodes, viewMode]);

  const daySummary = useMemo(() => {
    if (viewMode !== 'day') {
      return null;
    }
    return dayLookup.get(dayKey)?.daily_summary ?? null;
  }, [dayLookup, dayKey, viewMode]);

  const dayHighlight = useMemo(() => {
    if (viewMode !== 'day') {
      return null;
    }
    return dayLookup.get(dayKey)?.highlight ?? null;
  }, [dayLookup, dayKey, viewMode]);

  useEffect(() => {
    if (!daySummary) {
      setDailySummaryEditOpen(false);
      setDailySummaryTitle('');
      setDailySummaryText('');
      setDailySummaryResetting(false);
      setDailySummaryError(null);
      setDailySummaryVoiceError(null);
      return;
    }
    setDailySummaryEditOpen(false);
    setDailySummaryTitle(daySummary.title || '');
    setDailySummaryText(daySummary.summary || '');
    setDailySummaryResetting(false);
    setDailySummaryError(null);
    setDailySummaryVoiceError(null);
  }, [daySummary?.context_id]);

  const loadDayItems = async (reset = false) => {
    if (dayLoading && !reset) {
      return;
    }
    setDayLoading(true);
    setDayError(null);
    try {
      const nextOffset = reset ? 0 : dayOffset;
      const tzOffsetMinutes = getTimeZoneOffsetMinutes(anchorDate, timeZone);
      const query = new URLSearchParams({
        start_date: formatDateKey(anchorDate, timeZone),
        end_date: formatDateKey(anchorDate, timeZone),
        limit: DAY_PAGE_SIZE.toString(),
        offset: nextOffset.toString(),
        tz_offset_minutes: tzOffsetMinutes.toString(),
      });
      const data = await apiGet<TimelineItemsResponse>(`/timeline/items?${query.toString()}`);
      setDayItems((prev) => (reset ? data.items : [...prev, ...data.items]));
      setDayTotal(data.total);
      setDayOffset(nextOffset + data.items.length);
    } catch (err) {
      setDayError(err instanceof Error ? err.message : t('Failed to load memories.'));
    } finally {
      setDayLoading(false);
    }
  };

  const loadAllItems = async (reset = false) => {
    if (allLoading) {
      return;
    }
    setAllLoading(true);
    setAllError(null);
    try {
      const nextOffset = reset ? 0 : allOffset;
      const query = new URLSearchParams({
        limit: ALL_PAGE_SIZE.toString(),
        offset: nextOffset.toString(),
      });
      const data = await apiGet<TimelineItemsResponse>(`/timeline/items?${query.toString()}`);
      setAllItems((prev) => (reset ? data.items : [...prev, ...data.items]));
      setAllTotal(data.total);
      setAllOffset(nextOffset + data.items.length);
    } catch (err) {
      setAllError(err instanceof Error ? err.message : t('Failed to load memories.'));
    } finally {
      setAllLoading(false);
    }
  };

  useEffect(() => {
    if (viewMode !== 'all') {
      return;
    }
    loadAllItems(true);
  }, [viewMode, reloadKey]);

  useEffect(() => {
    if (viewMode !== 'day') {
      return;
    }
    setDayItems([]);
    setDayOffset(0);
    setDayTotal(0);
    setDayError(null);
    void loadDayItems(true);
  }, [viewMode, dayKey, reloadKey]);

  useEffect(() => {
    setHighlightError(null);
    setHighlightSavingId(null);
    setHighlightClearing(false);
  }, [dayKey, viewMode]);

  useEffect(() => {
    setUploadOpen(false);
    setUploadFiles([]);
    setUploadError(null);
    setUploadSuccess(null);
    setUploadedCount(0);
    setTimeMode('file');
  }, [dayKey]);

  useEffect(() => {
    setEpisodeVisibleCount(EPISODES_PAGE_SIZE);
  }, [dayKey, viewMode]);

  const uploadStart = useMemo(
    () => buildDateWithTime(anchorDate, uploadStartTime, timeZone),
    [anchorDate, uploadStartTime, timeZone]
  );
  const durationHours = useMemo(() => Math.max(0, Number(uploadDurationHours) || 0), [uploadDurationHours]);
  const uploadEnd = useMemo(
    () => new Date(uploadStart.getTime() + durationHours * 60 * 60 * 1000),
    [uploadStart, durationHours]
  );

  const episodeItemIds = useMemo(() => {
    const ids = new Set<string>();
    dayEpisodes.forEach((episode) => {
      episode.source_item_ids.forEach((itemId) => ids.add(itemId));
    });
    return ids;
  }, [dayEpisodes]);

  const sortedDayItems = useMemo(() => {
    const items = dayItems.filter((item) => !episodeItemIds.has(item.id));
    items.sort((a, b) => {
      const aTime = a.captured_at ? new Date(a.captured_at).getTime() : 0;
      const bTime = b.captured_at ? new Date(b.captured_at).getTime() : 0;
      return aTime - bTime;
    });
    return items;
  }, [dayItems, episodeItemIds]);

  const sortedDayEpisodes = useMemo(() => {
    const episodes = [...dayEpisodes];
    episodes.sort((a, b) => {
      const aTime = a.start_time_utc ? new Date(a.start_time_utc).getTime() : 0;
      const bTime = b.start_time_utc ? new Date(b.start_time_utc).getTime() : 0;
      return aTime - bTime;
    });
    return episodes;
  }, [dayEpisodes]);

  const episodeCards = useMemo<EpisodeCard[]>(() => {
    const base = showEpisodes ? sortedDayEpisodes.map((episode) => ({ ...episode, isSynthetic: false })) : [];
    const singles: EpisodeCard[] = sortedDayItems.map((item) => ({
      episode_id: `single-${item.id}`,
      title: buildLabel(item),
      summary: showCaptions
        ? item.caption || item.original_filename || t('Single memory')
        : '',
      context_type: 'single_memory',
      start_time_utc: item.captured_at,
      end_time_utc: item.captured_at,
      item_count: 1,
      source_item_ids: [item.id],
      context_ids: [],
      preview_url: getThumbnail(item) || undefined,
      isSynthetic: true,
      syntheticItemId: item.id,
    }));
    const combined = [...base, ...singles];
    combined.sort((a, b) => {
      const aTime = a.start_time_utc ? new Date(a.start_time_utc).getTime() : 0;
      const bTime = b.start_time_utc ? new Date(b.start_time_utc).getTime() : 0;
      return aTime - bTime;
    });
    return combined;
  }, [showCaptions, showEpisodes, sortedDayEpisodes, sortedDayItems]);

  const visibleDayEpisodes = useMemo(
    () => episodeCards.slice(0, episodeVisibleCount),
    [episodeCards, episodeVisibleCount]
  );

  const hasMoreEpisodes = useMemo(
    () => episodeCards.length > episodeVisibleCount,
    [episodeCards.length, episodeVisibleCount]
  );

  const hasMoreAllItems = useMemo(
    () => allItems.length < allTotal,
    [allItems.length, allTotal]
  );

  const hasMoreDayItems = useMemo(
    () => dayItems.length < dayTotal,
    [dayItems.length, dayTotal]
  );

  const dayStats = useMemo(() => {
    const totals: Record<string, number> = {
      photo: 0,
      video: 0,
      audio: 0,
      document: 0,
    };
    dayItems.forEach((item) => {
      totals[item.item_type] = (totals[item.item_type] ?? 0) + 1;
    });
    return totals;
  }, [dayItems]);

  const dayItemThumbnails = useMemo(() => {
    const map = new Map<string, string>();
    if (viewMode !== 'day') {
      return map;
    }
    dayItems.forEach((item) => {
      const thumbnail = getThumbnail(item);
      if (thumbnail) {
        map.set(item.id, thumbnail);
      }
    });
    return map;
  }, [dayItems, viewMode]);

  const getEpisodePreview = (episode: TimelineEpisode) => {
    if (episode.preview_url) {
      return episode.preview_url;
    }
    if (viewMode !== 'day') {
      return null;
    }
    for (const itemId of episode.source_item_ids) {
      const thumb = dayItemThumbnails.get(itemId);
      if (thumb) {
        return thumb;
      }
    }
    return null;
  };

  const memoryCount = useMemo(() => episodeCards.length, [episodeCards.length]);

  const hasItems = useMemo(() => days.some(day => day.items.length > 0), [days]);
  const rangeDates = useMemo(
    () => buildDateRange(range.start, range.end, timeZone),
    [range, timeZone]
  );
  const monthGrid = useMemo(
    () => buildMonthGrid(anchorDate, timeZone),
    [anchorDate, timeZone]
  );

  const rangeTotal = useMemo(
    () => days.reduce((sum, day) => sum + day.item_count, 0),
    [days]
  );

  const monthTotals = useMemo(() => {
    const totals = Array.from({ length: 12 }, () => 0);
    days.forEach((day) => {
      const parts = parseDateKey(day.date);
      if (!parts) {
        return;
      }
      totals[parts.month - 1] += day.item_count;
    });
    return totals;
  }, [days]);

  const monthPreviewMap = useMemo(() => {
    const map = new Map<number, { thumbnail: string; dayKey: string; priority: number; count: number; timestamp: number }>();
    days.forEach((day) => {
      const parts = parseDateKey(day.date);
      if (!parts) {
        return;
      }
      const thumbnail = getDayThumbnail(day, showHighlights);
      if (!thumbnail) {
        return;
      }
      const priority = showHighlights && day.highlight?.thumbnail_url ? 2 : 1;
      const count = day.item_count;
      const timestamp = Date.UTC(parts.year, parts.month - 1, parts.day);
      const existing = map.get(parts.month - 1);
      if (
        !existing
        || priority > existing.priority
        || (priority === existing.priority && timestamp > existing.timestamp)
        || (priority === existing.priority && timestamp === existing.timestamp && count > existing.count)
      ) {
        map.set(parts.month - 1, { thumbnail, dayKey: day.date, priority, count, timestamp });
      }
    });
    return map;
  }, [days, showHighlights]);

  const maxMonthTotal = useMemo(
    () => Math.max(1, ...monthTotals),
    [monthTotals]
  );

  const weekdayLabels = useMemo(() => {
    const base = startOfWeek(anchorDate, timeZone);
    return Array.from({ length: 7 }, (_, index) => {
      const date = addDaysZoned(base, index, timeZone);
      return formatDayLabel(date, locale, timeZone);
    });
  }, [anchorDate, locale, timeZone]);

  const removeItem = (itemId: string) => {
    setDays((prev) =>
      prev
        .map((day) => {
          const remaining = day.items.filter((item) => item.id !== itemId);
          const nextHighlight =
            day.highlight && day.highlight.item_id === itemId ? null : day.highlight ?? null;
          return { ...day, items: remaining, item_count: remaining.length, highlight: nextHighlight };
        })
        .filter((day) => day.items.length > 0)
    );
    setDayItems((prev) => prev.filter((item) => item.id !== itemId));
    setDayTotal((prev) => Math.max(0, prev - 1));
    setAllItems((prev) => prev.filter((item) => item.id !== itemId));
    setAllTotal((prev) => Math.max(0, prev - 1));
    if (selectedItemId === itemId) {
      setSelectedItemId(null);
      setDetail(null);
    }
  };

  const setDayHighlight = async (item: TimelineItem) => {
    const thumbnail = getThumbnail(item);
    if (!thumbnail) {
      setHighlightError(t('This memory does not have a preview thumbnail yet.'));
      return;
    }
    setHighlightSavingId(item.id);
    setHighlightError(null);
    try {
      await apiPost('/timeline/highlights', {
        highlight_date: dayKey,
        item_id: item.id,
        tz_offset_minutes: getTimeZoneOffsetMinutes(anchorDate, timeZone),
      });
      setDays((prev) =>
        prev.map((day) =>
          day.date === dayKey
            ? {
                ...day,
                highlight: {
                  item_id: item.id,
                  item_type: item.item_type,
                  thumbnail_url: thumbnail,
                },
              }
            : day
        )
      );
    } catch (err) {
      setHighlightError(err instanceof Error ? err.message : t('Failed to save highlight.'));
    } finally {
      setHighlightSavingId(null);
    }
  };

  const clearDayHighlight = async () => {
    setHighlightClearing(true);
    setHighlightError(null);
    try {
      await apiDelete(`/timeline/highlights/${dayKey}`);
      setDays((prev) =>
        prev.map((day) => (day.date === dayKey ? { ...day, highlight: null } : day))
      );
    } catch (err) {
      setHighlightError(err instanceof Error ? err.message : t('Failed to clear highlight.'));
    } finally {
      setHighlightClearing(false);
    }
  };

  const applyDailySummary = (updated: TimelineDailySummary, previousDate?: string) => {
    setDays((prev) =>
      prev.map((day) => {
        if (day.date === updated.summary_date) {
          return { ...day, daily_summary: updated };
        }
        if (previousDate && day.date === previousDate) {
          return { ...day, daily_summary: null };
        }
        return day;
      })
    );
  };

  const handleDailySummarySave = async () => {
    if (!daySummary) {
      return;
    }
    setDailySummarySaving(true);
    setDailySummaryError(null);
    try {
      const payload = {
        title: dailySummaryTitle,
        summary: dailySummaryText,
        tz_offset_minutes: getTimeZoneOffsetMinutes(anchorDate, timeZone),
      };
      const updated = await apiPatch<TimelineDailySummary>(
        `/timeline/daily-summaries/${daySummary.context_id}`,
        payload
      );
      applyDailySummary(updated, daySummary.summary_date);
      setDailySummaryEditOpen(false);
    } catch (err) {
      setDailySummaryError(err instanceof Error ? err.message : t('Failed to update daily summary.'));
    } finally {
      setDailySummarySaving(false);
    }
  };

  const handleDailySummaryVoice = async (file: File) => {
    if (!daySummary) {
      return;
    }
    setDailySummaryVoiceLoading(true);
    setDailySummaryVoiceError(null);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('tz_offset_minutes', getTimeZoneOffsetMinutes(anchorDate, timeZone).toString());
      const updated = await apiPostForm<TimelineDailySummary>(
        `/timeline/daily-summaries/${daySummary.context_id}/voice`,
        form
      );
      applyDailySummary(updated, daySummary.summary_date);
      setDailySummaryTitle(updated.title || '');
      setDailySummaryText(updated.summary || '');
      setDailySummaryEditOpen(true);
    } catch (err) {
      setDailySummaryVoiceError(err instanceof Error ? err.message : t('Voice update failed.'));
    } finally {
      setDailySummaryVoiceLoading(false);
    }
  };

  const handleDailySummaryReset = async () => {
    if (!daySummary) {
      return;
    }
    setDailySummaryResetting(true);
    setDailySummaryError(null);
    try {
      const updated = await apiPost<TimelineDailySummary>(
        `/timeline/daily-summaries/${daySummary.context_id}/reset`,
        { tz_offset_minutes: getTimeZoneOffsetMinutes(anchorDate, timeZone) }
      );
      applyDailySummary(updated, daySummary.summary_date);
      setDailySummaryTitle(updated.title || '');
      setDailySummaryText(updated.summary || '');
      setDailySummaryEditOpen(false);
    } catch (err) {
      setDailySummaryError(err instanceof Error ? err.message : t('Failed to reset daily summary.'));
    } finally {
      setDailySummaryResetting(false);
    }
  };

  useEffect(() => {
    const trimmed = searchQuery.trim();
    if (!trimmed) {
      setSearchResults([]);
      setSearchError(null);
      setSearchLoading(false);
      return;
    }
    let mounted = true;
    const handle = window.setTimeout(async () => {
      setSearchLoading(true);
      setSearchError(null);
      try {
        const data = await apiGet<SearchResponse>(`/search?q=${encodeURIComponent(trimmed)}&limit=8`);
        if (mounted) {
          setSearchResults(data.results || []);
        }
      } catch (err) {
        if (mounted) {
          setSearchError(err instanceof Error ? err.message : t('Search failed.'));
        }
      } finally {
        if (mounted) {
          setSearchLoading(false);
        }
      }
    }, 350);
    return () => {
      mounted = false;
      window.clearTimeout(handle);
    };
  }, [searchQuery]);

  useEffect(() => {
    if (!pendingSelection) {
      return;
    }
    if (pendingSelection.episodeContextId) {
      const match = days
        .flatMap((day) => day.episodes ?? [])
        .find((episode) => episode.context_ids?.includes(pendingSelection.episodeContextId ?? ''));
      if (match) {
        setSelectedEpisodeId(match.episode_id);
        setSelectedItemId(null);
        setPendingSelection(null);
        return;
      }
    }
    if (pendingSelection.itemId) {
      const match = days
        .flatMap((day) => day.episodes ?? [])
        .find((episode) => episode.source_item_ids?.includes(pendingSelection.itemId ?? ''));
      if (match) {
        setSelectedEpisodeId(match.episode_id);
        setSelectedItemId(null);
        setPendingSelection(null);
        return;
      }
      setSelectedItemId(pendingSelection.itemId);
      setSelectedEpisodeId(null);
      setPendingSelection(null);
    }
  }, [days, pendingSelection]);

  const handleDelete = async (itemId: string) => {
    if (!confirm(t('Delete this memory? This will remove it from storage and search.'))) {
      return;
    }
    setDeletingId(itemId);
    try {
      await apiDelete(`/timeline/items/${itemId}`);
      removeItem(itemId);
      setReloadKey((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Failed to delete item.'));
    } finally {
      setDeletingId(null);
    }
  };

  const handleEpisodeSave = async () => {
    if (!selectedEpisodeId || !episodeDetail || episodeEditSaving) {
      return;
    }
    setEpisodeEditSaving(true);
    setEpisodeEditError(null);
    try {
      const payload = {
        title: episodeEditTitle.trim(),
        summary: episodeEditSummary.trim(),
        context_type: 'activity_context',
      };
      const updated = await apiPatch<TimelineEpisodeDetail>(
        `/timeline/episodes/${selectedEpisodeId}`,
        payload
      );
      setEpisodeDetail(updated);
      setEpisodeEditOpen(false);
      setReloadKey((value) => value + 1);
    } catch (err) {
      setEpisodeEditError(err instanceof Error ? err.message : t('Failed to update episode.'));
    } finally {
      setEpisodeEditSaving(false);
    }
  };

  const handleEpisodeCancel = () => {
    if (episodeDetail) {
      setEpisodeEditTitle(episodeDetail.title || '');
      setEpisodeEditSummary(episodeDetail.summary || '');
    }
    setEpisodeEditError(null);
    setEpisodeEditOpen(false);
  };

  const handleSearchSelect = (result: SearchResult) => {
    const eventTime = result.event_time_utc ? new Date(result.event_time_utc) : null;
    if (eventTime && !Number.isNaN(eventTime.getTime())) {
      setAnchorDate(toZonedDate(eventTime, timeZone));
      setViewMode('day');
    }
    const isDaily = result.context_type === 'daily_summary';
    if (isDaily) {
      setPendingSelection(null);
      setSelectedEpisodeId(null);
      setSelectedItemId(null);
      return;
    }
    const isEpisode = result.payload && result.payload['is_episode'] === true;
    if (isEpisode) {
      setPendingSelection({ episodeContextId: result.context_id });
      return;
    }
    const itemId = result.source_item_ids?.[0];
    if (itemId) {
      setPendingSelection({ itemId });
    }
  };

  const handleUploadFiles = (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) {
      return;
    }
    setUploadFiles((prev) => [...prev, ...Array.from(fileList)]);
    setUploadError(null);
    setUploadSuccess(null);
    setUploadedCount(0);
  };

  const removeUploadFile = (index: number) => {
    setUploadFiles((prev) => prev.filter((_, idx) => idx !== index));
  };

  const clearUploadFiles = () => {
    setUploadFiles([]);
    setUploadError(null);
    setUploadSuccess(null);
    setUploadedCount(0);
  };

  const handleManualUpload = async () => {
    if (uploadFiles.length === 0 || uploading) {
      return;
    }
    setUploading(true);
    setUploadError(null);
    setUploadSuccess(null);
    setUploadedCount(0);
    const overrideEnabled = timeMode === 'window';
    const durationMs = durationHours * 60 * 60 * 1000;
    const captureTimes = uploadFiles.map((_, index) => {
      if (!overrideEnabled) {
        return null;
      }
      if (uploadFiles.length === 1 || durationMs <= 0) {
        return uploadStart;
      }
      const offset = (durationMs * index) / (uploadFiles.length - 1);
      return new Date(uploadStart.getTime() + offset);
    });

    const newPending: string[] = [];
    const pathDate = formatDateKey(anchorDate, timeZone);
    try {
      for (const [index, file] of uploadFiles.entries()) {
        const contentType = file.type || 'application/octet-stream';
        const uploadMeta = await apiPost<UploadUrlResponse>('/storage/upload-url', {
          filename: file.name,
          content_type: contentType,
          prefix: 'uploads/ui',
          path_date: pathDate,
        });
        if (!uploadMeta.url) {
          throw new Error(`Upload URL missing for ${file.name}`);
        }

        const headers = { ...(uploadMeta.headers || {}), 'Content-Type': contentType };
        const uploadResponse = await fetch(uploadMeta.url, {
          method: 'PUT',
          headers,
          body: file,
        });
        if (!uploadResponse.ok) {
          const responseText = await uploadResponse.text();
          throw new Error(
            `Upload failed for ${file.name}: ${uploadResponse.status} ${responseText || ''}`.trim()
          );
        }

        const durationSec = await getMediaDuration(file);
        const ingestPayload: Record<string, unknown> = {
          storage_key: uploadMeta.key,
          item_type: inferItemType(file),
          content_type: contentType,
          original_filename: file.name,
          size_bytes: file.size,
          duration_sec: durationSec,
          client_tz_offset_minutes: getTimeZoneOffsetMinutes(new Date(), timeZone),
        };
        if (overrideEnabled && captureTimes[index]) {
          ingestPayload.captured_at = captureTimes[index]?.toISOString();
          ingestPayload.event_time_override = true;
          ingestPayload.event_time_window_start = uploadStart.toISOString();
          ingestPayload.event_time_window_end = uploadEnd.toISOString();
        }
        const ingestResponse = await apiPost<IngestResponse>('/upload/ingest', ingestPayload);
        if (ingestResponse?.item_id) {
          newPending.push(ingestResponse.item_id);
        }

        setUploadedCount((count) => count + 1);
      }

      if (overrideEnabled) {
        setUploadSuccess(
          `Queued ${uploadFiles.length} upload(s) for ${formatDate(anchorDate, locale, timeZone)} (${formatClockTime(uploadStart, locale, timeZone)} - ${formatClockTime(uploadEnd, locale, timeZone)}).`
        );
      } else {
        setUploadSuccess(`Queued ${uploadFiles.length} upload(s) using file timestamps.`);
      }
      setUploadFiles([]);
      if (newPending.length > 0) {
        setPendingUploadIds((prev) => [...prev, ...newPending]);
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : t('Upload failed.'));
    } finally {
      setUploading(false);
    }
  };

  const renderUploadIcon = (file: File) => {
    if (file.type.startsWith('image/')) {
      return <ImageIcon className="h-4 w-4 text-slate-400" />;
    }
    if (file.type.startsWith('video/')) {
      return <Video className="h-4 w-4 text-slate-400" />;
    }
    if (file.type.startsWith('audio/')) {
      return <Mic className="h-4 w-4 text-slate-400" />;
    }
    return <FileText className="h-4 w-4 text-slate-400" />;
  };

  useEffect(() => {
    if (pendingUploadIds.length === 0) {
      return;
    }
    let cancelled = false;
    let timeoutId: number | null = null;
    let attempts = 0;

    const poll = async () => {
      attempts += 1;
      try {
        const results = await Promise.all(
          pendingUploadIds.map((id) =>
            apiGet<TimelineItemDetail>(`/timeline/items/${id}`).catch(() => null)
          )
        );
        if (cancelled) {
          return;
        }
        const statuses = results.map((item) => {
          if (!item) return 'processing';
          return item.processing_status || (item.processed ? 'completed' : 'processing');
        });
        const isDone = statuses.map((status) => status === 'completed' || status === 'failed');
        const remaining = pendingUploadIds.filter((_, index) => !isDone[index]);
        if (remaining.length === 0) {
          const failedCount = statuses.filter((status) => status === 'failed').length;
          setPendingUploadIds([]);
          setReloadKey((value) => value + 1);
          if (failedCount > 0) {
            setUploadError(`${failedCount} upload(s) failed to process.`);
          }
          return;
        }
      } catch (err) {
        if (!cancelled && attempts >= 12) {
          setUploadError(t('Uploads are still processing. Refresh later for updates.'));
          setPendingUploadIds([]);
          return;
        }
      }
      if (!cancelled && attempts < 60) {
        timeoutId = window.setTimeout(poll, 5000);
      } else if (!cancelled) {
        setUploadError(t('Uploads are still processing. Refresh later for updates.'));
        setPendingUploadIds([]);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [pendingUploadIds]);

  useEffect(() => {
    if (viewMode !== 'day') {
      setSelectedItemId(null);
      setDetail(null);
      setSelectedEpisodeId(null);
      setEpisodeDetail(null);
      return;
    }
    if (!episodeCards.length) {
      setSelectedItemId(null);
      setDetail(null);
      setSelectedEpisodeId(null);
      setEpisodeDetail(null);
      return;
    }
    if (selectedEpisodeId && sortedDayEpisodes.some((episode) => episode.episode_id === selectedEpisodeId)) {
      return;
    }
    if (selectedItemId && dayItems.some((item) => item.id === selectedItemId)) {
      return;
    }
    const nextCard = episodeCards[0];
    if (nextCard?.isSynthetic && nextCard.syntheticItemId) {
      setSelectedItemId(nextCard.syntheticItemId);
      setSelectedEpisodeId(null);
      return;
    }
    if (nextCard) {
      setSelectedEpisodeId(nextCard.episode_id);
      setSelectedItemId(null);
    }
  }, [episodeCards, sortedDayEpisodes, dayItems, viewMode, selectedItemId, selectedEpisodeId]);

  useEffect(() => {
    if (!selectedItemId) {
      setDetail(null);
      return;
    }
    let mounted = true;
    setDetailLoading(true);
    setDetailError(null);
    const load = async () => {
      try {
        const data = await apiGet<TimelineItemDetail>(`/timeline/items/${selectedItemId}`);
        if (mounted) {
          setDetail(data);
        }
      } catch (err) {
        if (mounted) {
          setDetailError(err instanceof Error ? err.message : t('Failed to load memory detail.'));
        }
      } finally {
        if (mounted) {
          setDetailLoading(false);
        }
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, [selectedItemId]);

  useEffect(() => {
    if (!selectedEpisodeId) {
      setEpisodeDetail(null);
      return;
    }
    let mounted = true;
    setEpisodeLoading(true);
    setEpisodeError(null);
    const load = async () => {
      try {
        const data = await apiGet<TimelineEpisodeDetail>(`/timeline/episodes/${selectedEpisodeId}`);
        if (mounted) {
          setEpisodeDetail(data);
        }
      } catch (err) {
        if (mounted) {
          setEpisodeError(err instanceof Error ? err.message : t('Failed to load episode detail.'));
        }
      } finally {
        if (mounted) {
          setEpisodeLoading(false);
        }
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, [selectedEpisodeId]);

  useEffect(() => {
    setEpisodeEditOpen(false);
    setEpisodeEditError(null);
  }, [selectedEpisodeId]);

  useEffect(() => {
    if (!episodeDetail || episodeEditOpen) {
      return;
    }
    setEpisodeEditTitle(episodeDetail.title || '');
    setEpisodeEditSummary(episodeDetail.summary || '');
  }, [episodeDetail, episodeEditOpen]);

  const moveAnchor = (direction: number) => {
    if (viewMode === 'all') {
      return;
    }
    if (viewMode === 'day') {
      setAnchorDate(addDaysZoned(anchorDate, direction, timeZone));
      return;
    } else if (viewMode === 'week') {
      setAnchorDate(addDaysZoned(anchorDate, direction * 7, timeZone));
      return;
    } else if (viewMode === 'month') {
      setAnchorDate(addMonthsZoned(anchorDate, direction, timeZone));
      return;
    } else {
      setAnchorDate(addYearsZoned(anchorDate, direction, timeZone));
      return;
    }
  };

  const handleToday = () => {
    setAnchorDate(toZonedDate(new Date(), timeZone));
    setViewMode('day');
  };

  return (
    <PageMotion className="h-full overflow-y-auto p-4 md:p-8">
      <div className="relative overflow-hidden rounded-[32px] border border-white/70 bg-gradient-to-br from-slate-50 via-white to-slate-100 shadow-[0_40px_120px_-60px_rgba(15,23,42,0.45)] dark:border-slate-800 dark:from-slate-950 dark:via-slate-900 dark:to-slate-950">
        <div className="absolute -top-32 -left-24 h-64 w-64 rounded-full bg-blue-200/40 blur-3xl dark:bg-blue-900/30" />
        <div className="absolute -bottom-32 -right-20 h-64 w-64 rounded-full bg-indigo-200/40 blur-3xl dark:bg-indigo-900/30" />
        <div className="relative z-10 space-y-6 p-6 md:p-10">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h1 className="text-3xl font-semibold tracking-tight text-slate-900">{t('Timeline')}</h1>
              <p className="text-sm text-slate-600">
                {t('Move through your day, week, month, and year of memories.')}
              </p>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <div className="flex items-center gap-1 rounded-full border border-white/70 bg-white/70 p-1 shadow-sm backdrop-blur">
                {VIEW_MODES.map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setViewMode(mode)}
                    className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-all ${
                      viewMode === mode
                        ? 'bg-slate-900 text-white shadow'
                        : 'text-slate-600 hover:bg-white'
                    }`}
                  >
                    {viewLabels[mode]}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2 rounded-full border border-white/70 bg-white/70 px-3 py-1.5 text-xs shadow-sm backdrop-blur">
                <Search className="h-4 w-4 text-slate-400" />
                <input
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder={t('Search memories')}
                  className="w-44 bg-transparent text-xs text-slate-700 placeholder:text-slate-400 focus:outline-none"
                />
                {searchQuery && (
                  <button
                    type="button"
                    onClick={() => setSearchQuery('')}
                    className="text-slate-400 hover:text-slate-600"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              <div className="flex items-center gap-2 rounded-full border border-white/70 bg-white/70 px-2 py-1.5 text-sm shadow-sm backdrop-blur">
                <button
                  type="button"
                  onClick={() => moveAnchor(-1)}
                  className="rounded-full p-1 text-slate-500 hover:bg-white"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <span className="flex items-center gap-2 px-1 text-xs font-semibold text-slate-700">
                  <Calendar className="h-4 w-4 text-slate-400" />
                  {formatRangeLabel(viewMode, anchorDate, locale, timeZone)}
                </span>
                <button
                  type="button"
                  onClick={() => moveAnchor(1)}
                  className="rounded-full p-1 text-slate-500 hover:bg-white"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={handleToday}
                  className="rounded-full px-2 text-[11px] font-semibold text-slate-600 hover:text-slate-900"
                >
                  {t('Today')}
                </button>
              </div>
            </div>
          </div>

          {searchQuery.trim() && (
            <div className="rounded-2xl border border-white/70 bg-white/80 p-4 shadow-sm backdrop-blur">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold text-slate-900">{t('Search results')}</h2>
                  <p className="text-xs text-slate-500">
                    {searchLoading
                      ? t('Searching...')
                      : t('{count} result(s)', { count: searchResults.length })}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  className="text-xs font-semibold text-slate-500 hover:text-slate-700"
                >
                  {t('Clear')}
                </button>
              </div>
              {searchError && <div className="mt-3 text-xs text-red-600">{searchError}</div>}
              {!searchLoading && !searchError && searchResults.length === 0 && (
                <div className="mt-3 text-xs text-slate-500">{t('No matches found.')}</div>
              )}
              {searchResults.length > 0 && (
                <div className="mt-3 space-y-2">
                  {searchResults.map((result) => {
                    const isEpisode = result.payload && result.payload['is_episode'] === true;
                    const isDaily = result.context_type === 'daily_summary';
                    const label = isDaily
                      ? t('Daily summary')
                      : isEpisode
                      ? t('Episode')
                      : t('Memory');
                    return (
                      <button
                        key={result.context_id}
                        type="button"
                        onClick={() => handleSearchSelect(result)}
                        className="w-full rounded-2xl border border-slate-100 bg-white px-4 py-3 text-left hover:shadow"
                      >
                        <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-wide text-slate-400">
                          <span>{label}</span>
                          {result.context_type && !isDaily && (
                            <span>{formatContextType(result.context_type)}</span>
                          )}
                          {result.event_time_utc && (
                            <span>{formatDate(result.event_time_utc, locale, timeZone)}</span>
                          )}
                        </div>
                        <p className="mt-1 text-sm font-semibold text-slate-900">
                          {result.title || t('Untitled result')}
                        </p>
                        {showCaptions && result.summary && (
                          <p className="mt-1 line-clamp-2 text-xs text-slate-600">{result.summary}</p>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {loading && viewMode !== 'all' && (
            <div className="rounded-2xl border border-white/70 bg-white/80 p-6 text-sm text-slate-500 shadow-sm backdrop-blur">
              {t('Loading timeline...')}
            </div>
          )}

          {error && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {!loading && !error && !hasItems && viewMode !== 'all' && (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-white/70 px-6 py-14 text-center text-sm text-slate-500 shadow-sm backdrop-blur">
              {t('No memories yet. Upload something to start your timeline.')}
            </div>
          )}

          {!loading && !error && viewMode !== 'day' && viewMode !== 'all' && hasItems && (
            <div className="rounded-2xl border border-white/70 bg-white/80 p-6 shadow-sm backdrop-blur">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-slate-900">
                    {t('{label} overview', { label: viewLabels[viewMode] })}
                  </h2>
                  <p className="text-xs text-slate-500">
                    {t('{days} days, {memories} memories', {
                      days: rangeDates.length,
                      memories: rangeTotal,
                    })}
                  </p>
                </div>
              </div>

              {viewMode === 'week' && (
                <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-7">
                  {rangeDates.map((date) => {
                    const key = formatDateKey(date, timeZone);
                    const day = dayLookup.get(key);
                    const count = day?.item_count ?? 0;
                    const thumbnail = getDayThumbnail(day, showHighlights);
                    const previewType = (showHighlights ? day?.highlight?.item_type : null) || day?.items?.[0]?.item_type;
                    const hasHighlight = showHighlights && Boolean(day?.highlight);
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => {
                          setAnchorDate(date);
                          setViewMode('day');
                        }}
                        className={`group relative w-full overflow-hidden rounded-2xl border text-left transition-all ${
                          count > 0
                            ? 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-md'
                            : 'border-white/60 bg-slate-50/70 text-slate-400'
                        }`}
                      >
                        <div className="relative aspect-[4/3] w-full">
                          {thumbnail ? (
                            <>
                              <img
                                src={thumbnail}
                                alt={t('Preview')}
                                className="absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                                loading="lazy"
                              />
                              <div className="absolute inset-0 bg-gradient-to-t from-slate-900/70 via-slate-900/20 to-transparent" />
                            </>
                          ) : (
                            <div className="absolute inset-0 flex items-center justify-center bg-slate-100 text-slate-400">
                              {previewType === 'video' ? (
                                <Video className="h-5 w-5" />
                              ) : previewType === 'audio' ? (
                                <Mic className="h-5 w-5" />
                              ) : (
                                <ImageIcon className="h-5 w-5" />
                              )}
                            </div>
                          )}
                            <div className={`relative z-10 flex h-full flex-col justify-between p-3 ${thumbnail ? 'text-white' : 'text-slate-500'}`}>
                              <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wide">
                                <span>{formatDayLabel(date, locale, timeZone)}</span>
                                <span>{getDateParts(date, timeZone).day}</span>
                              </div>
                              <div className="flex items-center justify-between text-sm font-semibold">
                                <span>
                                  {count > 0
                                    ? t('{count} memories', { count })
                                    : t('No memories')}
                                </span>
                                {hasHighlight && (
                                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white/80 text-amber-500">
                                    <Star className="h-3 w-3 fill-amber-400" />
                                  </span>
                              )}
                            </div>
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}

              {viewMode === 'month' && (
                <div className="mt-5">
                  <div className="grid grid-cols-7 gap-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                    {weekdayLabels.map((label) => (
                      <span key={label} className="text-center">
                        {label}
                      </span>
                    ))}
                  </div>
                <div className="mt-2 grid grid-cols-7 gap-2">
                  {monthGrid.map((date) => {
                    const key = formatDateKey(date, timeZone);
                    const day = dayLookup.get(key);
                    const count = day?.item_count ?? 0;
                    const isCurrent = isSameMonth(date, anchorDate, timeZone);
                    const thumbnail = getDayThumbnail(day, showHighlights);
                    const previewType = (showHighlights ? day?.highlight?.item_type : null) || day?.items?.[0]?.item_type;
                    const hasHighlight = showHighlights && Boolean(day?.highlight);
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => {
                          setAnchorDate(date);
                          setViewMode('day');
                        }}
                        className={`group relative w-full aspect-square overflow-hidden rounded-2xl border text-xs font-semibold transition-all ${
                          isCurrent
                            ? 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-md'
                            : 'border-white/60 bg-slate-50/70 text-slate-400'
                        }`}
                      >
                        {thumbnail ? (
                          <>
                            <img
                              src={thumbnail}
                              alt={t('Preview')}
                              className={`absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-105 ${
                                isCurrent ? '' : 'opacity-60'
                              }`}
                              loading="lazy"
                            />
                            <div className="absolute inset-0 bg-gradient-to-t from-slate-900/60 via-slate-900/10 to-transparent" />
                          </>
                        ) : (
                          <div className="absolute inset-0 flex items-center justify-center bg-slate-100 text-slate-400">
                            {previewType === 'video' ? (
                              <Video className="h-4 w-4" />
                            ) : previewType === 'audio' ? (
                              <Mic className="h-4 w-4" />
                            ) : (
                              <ImageIcon className="h-4 w-4" />
                            )}
                          </div>
                        )}
                        <div className={`relative z-10 flex h-full flex-col items-center justify-between p-2 ${thumbnail ? 'text-white' : 'text-slate-500'}`}>
                          <span className="text-sm">{getDateParts(date, timeZone).day}</span>
                          {count > 0 && (
                            <span className={`text-[10px] ${thumbnail ? 'text-white/80' : 'text-slate-500'}`}>
                              {count}
                            </span>
                          )}
                        </div>
                        {hasHighlight && (
                          <span className="absolute top-1.5 right-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-white/80 text-amber-500">
                            <Star className="h-3 w-3 fill-amber-400" />
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
              )}

              {viewMode === 'year' && (
                <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {Array.from({ length: 12 }, (_, index) => {
                    const total = monthTotals[index];
                    const percent = Math.round((total / maxMonthTotal) * 100);
                    const preview = monthPreviewMap.get(index);
                    const anchorYear = getDateParts(anchorDate, timeZone).year;
                    const monthDate = buildZonedDate(anchorYear, index + 1, 1, timeZone);
                    const monthLabel = formatMonthLabel(monthDate, locale, timeZone);
                    return (
                      <button
                        key={index}
                        type="button"
                        onClick={() => {
                          setAnchorDate(monthDate);
                          setViewMode('month');
                        }}
                        className={`group relative w-full min-h-[140px] overflow-hidden rounded-3xl border p-4 text-left transition-all ${
                          total > 0
                            ? 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-md'
                            : 'border-white/60 bg-slate-50/70 text-slate-400'
                        }`}
                      >
                        {preview?.thumbnail && (
                          <>
                            <img
                              src={preview.thumbnail}
                              alt={t('{month} preview', { month: monthLabel })}
                              className="absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                              loading="lazy"
                            />
                            <div className="absolute inset-0 bg-gradient-to-t from-slate-900/75 via-slate-900/25 to-transparent" />
                          </>
                        )}
                        <div className={`relative z-10 flex h-full flex-col justify-between ${preview?.thumbnail ? 'text-white' : 'text-slate-700'}`}>
                          <div className="flex items-center justify-between">
                            <span className="text-sm font-semibold">
                              {monthLabel}
                            </span>
                            <span className={`text-xs font-semibold ${preview?.thumbnail ? 'text-white/80' : 'text-slate-500'}`}>
                              {t('{count} memories', { count: total })}
                            </span>
                          </div>
                          <div className="mt-6">
                            <div className={`h-2 w-full rounded-full ${preview?.thumbnail ? 'bg-white/20' : 'bg-slate-100'}`}>
                              <div
                                className={`h-full rounded-full ${preview?.thumbnail ? 'bg-white' : 'bg-slate-900'}`}
                                style={{ width: `${percent}%` }}
                              />
                            </div>
                          </div>
                        </div>
                        {showHighlights && preview?.priority === 2 && (
                          <span className="absolute top-3 right-3 flex h-6 w-6 items-center justify-center rounded-full bg-white/80 text-amber-500">
                            <Star className="h-3.5 w-3.5 fill-amber-400" />
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {!error && viewMode === 'all' && (
            <div className="rounded-2xl border border-white/70 bg-white/80 p-6 shadow-sm backdrop-blur">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold text-slate-900">{t('All memories')}</h2>
                  <p className="text-xs text-slate-500">{t('{count} total', { count: allTotal })}</p>
                </div>
                <div className="text-xs text-slate-400">{t('Newest first')}</div>
              </div>
              {allError && <div className="mt-4 text-xs text-red-600">{allError}</div>}
              {allLoading && allItems.length === 0 && (
                <div className="mt-6 text-sm text-slate-500">{t('Loading memories...')}</div>
              )}
              {!allLoading && allItems.length === 0 && (
                <div className="mt-6 text-sm text-slate-500">{t('No memories yet.')}</div>
              )}
              {allItems.length > 0 && (
                <div className="mt-5 space-y-3">
                  {allItems.map((item) => {
                    const thumbnail = getThumbnail(item);
                    return (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => {
                          if (item.captured_at) {
                            const parsed = new Date(item.captured_at);
                            if (!Number.isNaN(parsed.getTime())) {
                              setAnchorDate(toZonedDate(parsed, timeZone));
                            }
                          }
                          setViewMode('day');
                          setSelectedEpisodeId(null);
                          setSelectedItemId(null);
                          setPendingSelection({ itemId: item.id });
                        }}
                        className="flex w-full items-center gap-4 rounded-2xl border border-white/60 bg-white/90 p-4 text-left transition-all hover:border-slate-200 hover:shadow"
                      >
                        <div className="relative h-14 w-14 flex-shrink-0 overflow-hidden rounded-xl bg-slate-100">
                          {thumbnail ? (
                            <img
                              src={thumbnail}
                              alt={buildLabel(item)}
                              className="h-full w-full object-cover"
                              loading="lazy"
                            />
                          ) : item.item_type === 'video' ? (
                            <div className="flex h-full w-full items-center justify-center text-slate-400">
                              <Video className="h-5 w-5" />
                            </div>
                          ) : item.item_type === 'audio' ? (
                            <div className="flex h-full w-full items-center justify-center text-slate-400">
                              <Mic className="h-5 w-5" />
                            </div>
                          ) : (
                            <div className="flex h-full w-full items-center justify-center text-slate-400">
                              <ImageIcon className="h-5 w-5" />
                            </div>
                          )}
                          {item.item_type === 'video' && (
                            <span className="absolute inset-0 flex items-center justify-center">
                              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-black/60 text-white">
                                <Play className="h-3 w-3" />
                              </span>
                            </span>
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-wide text-slate-400">
                            <span>{itemTypeLabels[item.item_type] ?? item.item_type}</span>
                            <span>{formatDate(item.captured_at, locale, timeZone)}</span>
                            {item.captured_at && (
                              <span>{formatTime(item.captured_at, locale, timeZone)}</span>
                            )}
                          </div>
                          <p className="mt-1 line-clamp-2 text-sm font-semibold text-slate-900">
                            {buildLabel(item)}
                          </p>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
              {hasMoreAllItems && (
                <button
                  type="button"
                  onClick={() => loadAllItems(false)}
                  disabled={allLoading}
                  className="mt-5 w-full rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                >
                  {allLoading ? t('Loading more...') : t('Load more memories')}
                </button>
              )}
            </div>
          )}

          {!loading && !error && viewMode === 'day' && (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-[340px_1fr]">
              <div className="rounded-2xl border border-white/70 bg-white/80 p-4 shadow-sm backdrop-blur">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-900">{t('Daily timeline')}</h2>
                    <p className="text-xs text-slate-500">
                      {t('{count} memories', { count: memoryCount })}
                      {dayTotal > 0 && dayItems.length < dayTotal
                        ? t(' · Showing {shown} of {total} items', {
                            shown: dayItems.length,
                            total: dayTotal,
                          })
                        : ''}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="flex flex-wrap gap-1 text-[10px]">
                      {dayStats.photo > 0 && (
                        <span className="rounded-full bg-slate-900 px-2 py-0.5 text-white">
                          {t('{count} photos', { count: dayStats.photo })}
                        </span>
                      )}
                      {dayStats.video > 0 && (
                        <span className="rounded-full bg-white px-2 py-0.5 text-slate-700">
                          {t('{count} videos', { count: dayStats.video })}
                        </span>
                      )}
                      {dayStats.audio > 0 && (
                        <span className="rounded-full bg-white px-2 py-0.5 text-slate-700">
                          {t('{count} audio', { count: dayStats.audio })}
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => setUploadOpen((open) => !open)}
                      className="flex items-center gap-1 rounded-full border border-slate-200 bg-white px-3 py-1 text-[10px] font-semibold text-slate-700 hover:border-slate-300"
                    >
                      <UploadCloud className="h-3.5 w-3.5" />
                      {uploadOpen ? t('Close uploader') : t('Upload for this day')}
                    </button>
                  </div>
                </div>

                {uploadOpen && (
                  <div className="mt-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-sm font-semibold text-slate-900">
                          {t('Upload memories for {date}', {
                            date: formatDate(anchorDate, locale, timeZone),
                          })}
                        </h3>
                        <p className="text-xs text-slate-500">
                          {t('Choose how we should timestamp the uploads for this day.')}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => setUploadOpen(false)}
                        className="text-slate-400 hover:text-slate-600"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                    <div className="mt-4 space-y-3">
                      <div className="flex flex-wrap items-center gap-2 rounded-full border border-slate-200 bg-slate-50 p-1 text-[11px] font-semibold text-slate-600">
                        <button
                          type="button"
                          onClick={() => setTimeMode('file')}
                          className={`rounded-full px-3 py-1 transition-colors ${
                            timeMode === 'file' ? 'bg-slate-900 text-white' : 'hover:bg-white'
                          }`}
                        >
                          {t('Use file time')}
                        </button>
                        <button
                          type="button"
                          onClick={() => setTimeMode('window')}
                          className={`rounded-full px-3 py-1 transition-colors ${
                            timeMode === 'window' ? 'bg-slate-900 text-white' : 'hover:bg-white'
                          }`}
                        >
                          {t('Set time window')}
                        </button>
                      </div>
                      {timeMode === 'file' ? (
                        <p className="text-xs text-slate-500">
                          {t('We use the file timestamp when available; otherwise the upload time is used.')}
                        </p>
                      ) : (
                        <div className="grid gap-3 sm:grid-cols-[minmax(0,180px)_minmax(0,180px)_minmax(0,1fr)]">
                          <label className="text-xs text-slate-500">
                            {t('Start time')}
                            <input
                              type="time"
                              value={uploadStartTime}
                              onChange={(event) => setUploadStartTime(event.target.value)}
                              className="mt-1 h-10 w-full min-w-[140px] rounded-lg border border-slate-200 px-3 text-sm text-slate-700"
                            />
                          </label>
                          <label className="text-xs text-slate-500">
                            {t('Duration (hours)')}
                            <input
                              type="number"
                              min="0.5"
                              step="0.5"
                              value={uploadDurationHours}
                              onChange={(event) => setUploadDurationHours(event.target.value)}
                              className="mt-1 h-10 w-full min-w-[120px] rounded-lg border border-slate-200 px-3 text-sm text-slate-700"
                            />
                          </label>
                          <div className="flex items-end text-xs text-slate-500">
                            {t('Window: {start} - {end}', {
                              start: formatClockTime(uploadStart, locale, timeZone),
                              end: formatClockTime(uploadEnd, locale, timeZone),
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="mt-4 flex flex-col gap-3">
                      <label className="flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-3 text-xs font-semibold text-slate-600 hover:border-slate-300 hover:bg-slate-100">
                        <UploadCloud className="h-4 w-4" />
                        {t('Select files')}
                        <input
                          type="file"
                          multiple
                          accept="image/*,video/*,audio/*"
                          className="hidden"
                          onChange={(event) => handleUploadFiles(event.target.files)}
                        />
                      </label>
                      {uploadFiles.length > 0 && (
                        <div className="space-y-2">
                          {uploadFiles.map((file, idx) => (
                            <div
                              key={`${file.name}-${idx}`}
                              className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600"
                            >
                              <div className="flex items-center gap-2 truncate">
                                {renderUploadIcon(file)}
                                <span className="truncate">{file.name}</span>
                              </div>
                              <button
                                type="button"
                                onClick={() => removeUploadFile(idx)}
                                className="text-slate-400 hover:text-slate-600"
                              >
                                <X className="h-3 w-3" />
                              </button>
                            </div>
                          ))}
                          <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
                            <button
                              type="button"
                              onClick={clearUploadFiles}
                              className="text-xs text-slate-500 hover:text-slate-700"
                            >
                              {t('Clear files')}
                            </button>
                            <button
                              type="button"
                              onClick={handleManualUpload}
                              disabled={uploading}
                              className="rounded-full bg-slate-900 px-4 py-1.5 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
                            >
                              {uploading
                                ? t('Uploading {current}/{total}', {
                                    current: uploadedCount,
                                    total: uploadFiles.length,
                                  })
                                : timeMode === 'window'
                                  ? t('Upload to this time window')
                                  : t('Upload with file timestamps')}
                            </button>
                          </div>
                        </div>
                      )}
                      {uploadError && <div className="text-xs text-red-600">{uploadError}</div>}
                      {uploadSuccess && <div className="text-xs text-green-600">{uploadSuccess}</div>}
                    </div>
                  </div>
                )}

                {showHighlights && (
                  <div className="mt-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-semibold text-slate-900">{t('Day highlight')}</p>
                        <p className="text-xs text-slate-500">
                          {t('Pick one memory to represent this day in week/month/year views.')}
                        </p>
                      </div>
                      {dayHighlight && (
                        <button
                          type="button"
                          onClick={clearDayHighlight}
                          disabled={highlightClearing}
                          className="text-[11px] font-semibold text-slate-500 hover:text-slate-700 disabled:opacity-60"
                        >
                          {highlightClearing ? t('Clearing...') : t('Clear')}
                        </button>
                      )}
                    </div>
                    <div className="mt-3 flex items-center gap-3 rounded-xl border border-dashed border-slate-200 bg-slate-50 px-3 py-2">
                      <div className="relative h-12 w-12 overflow-hidden rounded-lg bg-white">
                        {dayHighlight?.thumbnail_url ? (
                          <img
                            src={dayHighlight.thumbnail_url}
                            alt={t('Day highlight')}
                            className="h-full w-full object-cover"
                            loading="lazy"
                          />
                        ) : (
                          <div className="flex h-full w-full items-center justify-center text-slate-400">
                            <ImageIcon className="h-4 w-4" />
                          </div>
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-semibold text-slate-700">
                          {dayHighlight
                            ? t('Highlighted memory selected')
                            : t('No highlight selected')}
                        </p>
                        <p className="text-[11px] text-slate-500">
                          {dayHighlight
                            ? t('Use the star next to a memory to switch.')
                            : t('Use the star next to a memory to set one.')}
                        </p>
                      </div>
                    </div>
                    {highlightError && <div className="mt-2 text-xs text-red-600">{highlightError}</div>}
                  </div>
                )}

                {episodeCards.length > 0 && (
                  <div className="mt-4 space-y-3">
                    <div className="flex items-center justify-between text-xs text-slate-500">
                      <span className="font-semibold text-slate-700">
                        {showEpisodes ? t('Episodes') : t('Memories')}
                      </span>
                      <span>{episodeCards.length}</span>
                    </div>
                    {visibleDayEpisodes.map((episode) => {
                      const isSynthetic = Boolean(episode.isSynthetic);
                      const isActive = isSynthetic
                        ? selectedItemId === episode.syntheticItemId
                        : episode.episode_id === selectedEpisodeId;
                      const preview = getEpisodePreview(episode);
                      const label = isSynthetic ? t('Memory') : t('Episode');
                      return (
                        <button
                          key={episode.episode_id}
                          type="button"
                          onClick={() => {
                            if (isSynthetic && episode.syntheticItemId) {
                              setSelectedEpisodeId(null);
                              setSelectedItemId(episode.syntheticItemId);
                              return;
                            }
                            setSelectedEpisodeId(episode.episode_id);
                            setSelectedItemId(null);
                          }}
                          className={`w-full rounded-2xl border p-3 text-left transition-all ${
                            isActive
                              ? 'border-slate-900 bg-slate-900 text-white shadow-lg'
                              : 'border-white/60 bg-white/90 hover:border-slate-200 hover:shadow'
                          }`}
                        >
                          <div className="flex items-start gap-3">
                            <div className="h-12 w-12 flex-shrink-0 overflow-hidden rounded-xl bg-slate-100">
                              {preview ? (
                                <img
                                  src={preview}
                                  alt={episode.title}
                                  className="h-full w-full object-cover"
                                  loading="lazy"
                                />
                              ) : (
                                <div className="flex h-full w-full items-center justify-center text-slate-400">
                                  <ImageIcon className="h-4 w-4" />
                                </div>
                              )}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between text-[10px] uppercase tracking-wide">
                                <span className={isActive ? 'text-white/70' : 'text-slate-400'}>
                                  {label}
                                </span>
                                <span className={isActive ? 'text-white/70' : 'text-slate-400'}>
                                  {t('{count} items', { count: episode.item_count })}
                                </span>
                              </div>
                              <p className={`mt-1 text-sm font-semibold ${isActive ? 'text-white' : 'text-slate-900'}`}>
                                {episode.title}
                              </p>
                              <p className={`mt-1 text-xs ${isActive ? 'text-white/70' : 'text-slate-500'}`}>
                                {formatTimeRange(episode.start_time_utc, episode.end_time_utc, locale, timeZone)}
                              </p>
                              {showCaptions && episode.summary && (
                                <p className={`mt-1 line-clamp-2 text-xs ${isActive ? 'text-white/70' : 'text-slate-500'}`}>
                                  {episode.summary}
                                </p>
                              )}
                            </div>
                          </div>
                        </button>
                      );
                    })}
                    {hasMoreEpisodes && (
                      <button
                        type="button"
                        onClick={() => setEpisodeVisibleCount((value) => value + EPISODES_PAGE_SIZE)}
                        className="w-full rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
                      >
                        {t('Show more episodes')}
                      </button>
                    )}
                  </div>
                )}

                {dayError && (
                  <div className="mt-3 text-xs text-red-600">{dayError}</div>
                )}

                {dayLoading && dayItems.length === 0 && episodeCards.length === 0 ? (
                  <div className="py-8 text-center text-sm text-slate-500">
                    {t('Loading memories...')}
                  </div>
                ) : episodeCards.length === 0 ? (
                  <div className="py-8 text-center text-sm text-slate-500">
                    {t('No memories for this day.')}
                  </div>
                ) : null}
                {hasMoreDayItems && (
                  <button
                    type="button"
                    onClick={() => loadDayItems(false)}
                    disabled={dayLoading}
                    className="mt-4 w-full rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                  >
                    {dayLoading ? t('Loading more...') : t('Load more memories')}
                  </button>
                )}
              </div>

              <div className="rounded-2xl border border-white/70 bg-white/80 p-6 shadow-sm backdrop-blur">
                {viewMode === 'day' && daySummary && (
                  <div className="mb-6 rounded-2xl border border-slate-100 bg-white p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                        <FileText className="h-3 w-3" />
                        {t('Daily summary')}
                      </div>
                      <button
                        type="button"
                        onClick={() => setDailySummaryEditOpen((open) => !open)}
                        className="text-[11px] font-semibold text-slate-500 hover:text-slate-700"
                      >
                        {dailySummaryEditOpen ? t('Close editor') : t('Edit summary')}
                      </button>
                    </div>

                    {dailySummaryEditOpen ? (
                      <div className="mt-3 space-y-3">
                        <label className="text-xs text-slate-500">
                          {t('Title')}
                          <input
                            value={dailySummaryTitle}
                            onChange={(event) => setDailySummaryTitle(event.target.value)}
                            className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-700"
                          />
                        </label>
                        <label className="text-xs text-slate-500">
                          {t('Summary')}
                          <textarea
                            value={dailySummaryText}
                            onChange={(event) => setDailySummaryText(event.target.value)}
                            rows={4}
                            className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700"
                          />
                        </label>
                        <div className="flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            onClick={handleDailySummarySave}
                            disabled={dailySummarySaving || dailySummaryResetting}
                            className="rounded-full bg-slate-900 px-4 py-1.5 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-60"
                          >
                            {dailySummarySaving ? t('Saving...') : t('Save summary')}
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setDailySummaryEditOpen(false);
                              setDailySummaryTitle(daySummary.title || '');
                              setDailySummaryText(daySummary.summary || '');
                            }}
                            className="rounded-full border border-slate-200 px-4 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
                          >
                            {t('Cancel')}
                          </button>
                          <button
                            type="button"
                            onClick={handleDailySummaryReset}
                            disabled={dailySummaryResetting || dailySummarySaving}
                            className="rounded-full border border-slate-200 px-4 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                          >
                            {dailySummaryResetting ? t('Resetting...') : t('Reset to auto summary')}
                          </button>
                          <label className="ml-auto inline-flex cursor-pointer items-center gap-2 rounded-full border border-slate-200 px-3 py-1.5 text-[11px] text-slate-600 hover:bg-slate-50">
                            <input
                              type="file"
                              accept="audio/*"
                              className="hidden"
                              onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (file) {
                                  void handleDailySummaryVoice(file);
                                }
                                event.currentTarget.value = '';
                              }}
                            />
                            {dailySummaryVoiceLoading ? t('Transcribing...') : t('Upload voice note')}
                          </label>
                        </div>
                        <p className="text-[11px] text-slate-400">
                          {t('Voice updates replace the summary with a transcript.')}
                        </p>
                        {dailySummaryError && (
                          <div className="text-xs text-red-600">{dailySummaryError}</div>
                        )}
                        {dailySummaryVoiceError && (
                          <div className="text-xs text-red-600">{dailySummaryVoiceError}</div>
                        )}
                      </div>
                    ) : (
                      <>
                        <p className="mt-2 text-sm font-semibold text-slate-900">{daySummary.title}</p>
                        <p className="mt-2 text-sm text-slate-600">{daySummary.summary}</p>
                        {daySummary.keywords.length > 0 && (
                          <div className="mt-3 flex flex-wrap gap-1">
                            {daySummary.keywords.map((keyword) => (
                              <span
                                key={keyword}
                                className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500"
                              >
                                {keyword}
                              </span>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
                {selectedEpisodeId ? (
                  <>
                  {episodeLoading && (
                      <div className="text-sm text-slate-500">{t('Loading episode details...')}</div>
                  )}
                  {episodeError && (
                    <div className="text-sm text-red-600">{episodeError}</div>
                  )}
                  {!episodeLoading && !episodeDetail && (
                      <div className="text-sm text-slate-500">
                        {t('Select an episode to see details.')}
                      </div>
                  )}
                    {episodeDetail && (
                      <div className="space-y-6">
                        <div>
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                              <h3 className="text-lg font-semibold text-slate-900">{episodeDetail.title}</h3>
                              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                                <span>{formatDate(episodeDetail.start_time_utc || undefined, locale, timeZone)}</span>
                                <span className="rounded-full bg-slate-900 px-2 py-0.5 text-white">
                                  {t('Episode')}
                                </span>
                                <span className="rounded-full bg-white px-2 py-0.5 text-slate-600">
                                  {t('{count} items', { count: episodeDetail.source_item_ids.length })}
                                </span>
                              </div>
                            </div>
                            <button
                              type="button"
                              onClick={() => {
                                setEpisodeEditOpen(true);
                                setEpisodeEditError(null);
                              }}
                              disabled={episodeEditSaving || episodeEditOpen}
                              className="rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                            >
                              {episodeEditOpen ? t('Editing') : t('Edit episode')}
                            </button>
                          </div>
                          <p className="mt-2 text-sm text-slate-600">
                            {formatTimeRange(episodeDetail.start_time_utc, episodeDetail.end_time_utc, locale, timeZone)}
                          </p>
                          {episodeEditOpen ? (
                            <div className="mt-4 space-y-3 rounded-2xl border border-slate-100 bg-white p-4">
                              <label className="text-xs text-slate-500">
                                {t('Title')}
                                <input
                                  value={episodeEditTitle}
                                  onChange={(event) => setEpisodeEditTitle(event.target.value)}
                                  className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-700"
                                />
                              </label>
                              <label className="text-xs text-slate-500">
                                {t('Summary')}
                                <textarea
                                  value={episodeEditSummary}
                                  onChange={(event) => setEpisodeEditSummary(event.target.value)}
                                  rows={4}
                                  className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700"
                                />
                              </label>
                              <div className="flex flex-wrap gap-2">
                                <button
                                  type="button"
                                  onClick={handleEpisodeSave}
                                  disabled={episodeEditSaving}
                                  className="rounded-full bg-slate-900 px-4 py-1.5 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-60"
                                >
                                  {episodeEditSaving ? t('Saving...') : t('Save changes')}
                                </button>
                                <button
                                  type="button"
                                  onClick={handleEpisodeCancel}
                                  className="rounded-full border border-slate-200 px-4 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
                                >
                                  {t('Cancel')}
                                </button>
                              </div>
                              {episodeEditError && (
                                <div className="text-xs text-red-600">{episodeEditError}</div>
                              )}
                            </div>
                          ) : (
                            <p className="mt-2 text-sm text-slate-700">{episodeDetail.summary}</p>
                          )}
                        </div>

                        <div>
                          <h4 className="text-sm font-semibold text-slate-900">{t('Episode contexts')}</h4>
                          <div className="mt-3 grid gap-3 sm:grid-cols-2">
                            {episodeDetail.contexts.map((context, index) => (
                              <div key={`${context.context_type}-${index}`} className="rounded-2xl border border-slate-100 bg-white p-4">
                                <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-slate-400">
                                  <span>{formatContextType(context.context_type)}</span>
                                </div>
                                <p className="mt-2 text-sm font-semibold text-slate-900">{context.title}</p>
                                <p className="mt-1 text-xs text-slate-600">{context.summary}</p>
                                {context.keywords.length > 0 && (
                                  <div className="mt-3 flex flex-wrap gap-1">
                                    {context.keywords.map((keyword) => (
                                      <span key={keyword} className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">
                                        {keyword}
                                      </span>
                                    ))}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>

                        <div>
                          <h4 className="text-sm font-semibold text-slate-900">{t('Episode items')}</h4>
                          <div className="mt-3 grid gap-3 sm:grid-cols-2">
                            {episodeDetail.items.map((item) => (
                              <div
                                key={item.id}
                                role="button"
                                tabIndex={0}
                                onClick={() => {
                                  setSelectedEpisodeId(null);
                                  setSelectedItemId(item.id);
                                }}
                                onKeyDown={(event) => {
                                  if (event.key === 'Enter' || event.key === ' ') {
                                    event.preventDefault();
                                    setSelectedEpisodeId(null);
                                    setSelectedItemId(item.id);
                                  }
                                }}
                                className="flex items-center gap-3 rounded-2xl border border-slate-100 bg-white p-3 text-left hover:shadow"
                              >
                                <div className="h-12 w-12 overflow-hidden rounded-xl bg-slate-100">
                                  {item.item_type === 'video' && item.poster_url ? (
                                    <img
                                      src={item.poster_url}
                                      alt={buildLabel(item)}
                                      className="h-full w-full object-cover"
                                      loading="lazy"
                                    />
                                  ) : item.item_type === 'photo' && item.download_url ? (
                                    <img
                                      src={item.download_url}
                                      alt={buildLabel(item)}
                                      className="h-full w-full object-cover"
                                      loading="lazy"
                                    />
                                  ) : item.item_type === 'video' ? (
                                    <div className="flex h-full w-full items-center justify-center text-slate-400">
                                      <Video className="h-4 w-4" />
                                    </div>
                                  ) : item.item_type === 'audio' ? (
                                    <div className="flex h-full w-full items-center justify-center text-slate-400">
                                      <Mic className="h-4 w-4" />
                                    </div>
                                  ) : (
                                    <div className="flex h-full w-full items-center justify-center text-slate-400">
                                      <ImageIcon className="h-4 w-4" />
                                    </div>
                                  )}
                                </div>
                                <div className="min-w-0 flex-1">
                                  <p className="text-xs uppercase tracking-wide text-slate-400">
                                    {itemTypeLabels[item.item_type] ?? item.item_type}
                                  </p>
                                  <p className="mt-1 text-sm font-semibold text-slate-900 line-clamp-2">
                                    {buildLabel(item)}
                                  </p>
                                </div>
                                {showHighlights &&
                                  (() => {
                                    const thumbnail = getThumbnail(item);
                                    const canHighlight = Boolean(thumbnail);
                                    const isHighlighted = dayHighlight?.item_id === item.id;
                                    const isHighlighting = highlightSavingId === item.id;
                                    return (
                                      <button
                                        type="button"
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          if (!canHighlight || isHighlighting) {
                                            return;
                                          }
                                          void setDayHighlight(item);
                                        }}
                                        disabled={!canHighlight || isHighlighting}
                                        className={`ml-auto flex h-8 w-8 items-center justify-center rounded-full border transition ${
                                          isHighlighted
                                            ? 'border-amber-400 bg-amber-400 text-white'
                                            : 'border-slate-200 text-slate-500 hover:border-slate-300'
                                        } ${!canHighlight ? 'opacity-40' : ''}`}
                                        title={
                                          canHighlight
                                            ? isHighlighted
                                              ? t('Highlighted')
                                              : t('Set highlight')
                                            : t('No thumbnail available')
                                        }
                                      >
                                        {isHighlighting ? (
                                          <span className="text-[10px] font-semibold">{t('...')}</span>
                                        ) : (
                                          <Star className={`h-4 w-4 ${isHighlighted ? 'fill-white' : ''}`} />
                                        )}
                                      </button>
                                    );
                                  })()}
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    {detailLoading && (
                      <div className="text-sm text-slate-500">{t('Loading memory details...')}</div>
                    )}
                    {detailError && (
                      <div className="text-sm text-red-600">{detailError}</div>
                    )}
                    {!detailLoading && !detail && (
                      <div className="text-sm text-slate-500">
                        {t('Select a memory to see details.')}
                      </div>
                    )}
                    {detail && (
                      <div className="space-y-6">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <div>
                            <h3 className="text-lg font-semibold text-slate-900">{buildLabel(detail)}</h3>
                            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                              <span>{formatDate(detail.captured_at, locale, timeZone)}</span>
                              <span className="rounded-full bg-slate-900 px-2 py-0.5 text-white">
                                {itemTypeLabels[detail.item_type] ?? detail.item_type}
                              </span>
                              <span className="rounded-full bg-white px-2 py-0.5 text-slate-600">
                                {detail.processed ? t('Processed') : t('Processing')}
                              </span>
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            {showHighlights &&
                              (() => {
                                const thumbnail = getThumbnail(detail);
                                const canHighlight = Boolean(thumbnail);
                                const isHighlighted = dayHighlight?.item_id === detail.id;
                                const isHighlighting = highlightSavingId === detail.id;
                                return (
                                  <button
                                    type="button"
                                    onClick={() => {
                                      if (!canHighlight || isHighlighting) {
                                        return;
                                      }
                                      void setDayHighlight(detail);
                                    }}
                                    disabled={!canHighlight || isHighlighting}
                                    className={`flex items-center gap-1 rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                                      isHighlighted
                                        ? 'border-amber-400 bg-amber-400 text-white'
                                        : 'border-slate-200 text-slate-600 hover:bg-slate-50'
                                    } ${!canHighlight ? 'opacity-40' : ''}`}
                                    title={
                                      canHighlight
                                        ? isHighlighted
                                          ? t('Highlighted')
                                          : t('Set highlight')
                                        : t('No thumbnail available')
                                    }
                                  >
                                    {isHighlighting ? t('Saving...') : (
                                      <>
                                        <Star className={`h-3.5 w-3.5 ${isHighlighted ? 'fill-white' : ''}`} />
                                        {isHighlighted ? t('Highlighted') : t('Set highlight')}
                                      </>
                                    )}
                                  </button>
                                );
                              })()}
                            <button
                              type="button"
                              onClick={() => handleDelete(detail.id)}
                              className="rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
                              disabled={deletingId === detail.id}
                            >
                              {deletingId === detail.id ? t('Deleting...') : t('Delete')}
                            </button>
                          </div>
                        </div>

                        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-slate-50">
                          {detail.item_type === 'video' ? (
                            detail.download_url ? (
                              <video
                                src={detail.download_url}
                                className="w-full max-h-[360px]"
                                controls
                                preload="metadata"
                                poster={detail.poster_url || undefined}
                                playsInline
                              />
                            ) : (
                              <div className="p-6 text-slate-400">{t('Video unavailable.')}</div>
                            )
                          ) : detail.item_type === 'audio' ? (
                            detail.download_url ? (
                              <div className="bg-white p-4">
                                <audio src={detail.download_url} controls className="w-full" preload="metadata" />
                              </div>
                            ) : (
                              <div className="p-6 text-slate-400">{t('Audio unavailable.')}</div>
                            )
                          ) : detail.download_url ? (
                            <img
                              src={detail.download_url}
                              alt={buildLabel(detail)}
                              className="w-full object-cover"
                              loading="lazy"
                            />
                          ) : (
                            <div className="p-6 text-slate-400">{t('Preview unavailable.')}</div>
                          )}
                        </div>

                        <div>
                          <h4 className="text-sm font-semibold text-slate-900">{t('Contexts')}</h4>
                          {detail.contexts.length === 0 ? (
                            <p className="mt-2 text-xs text-slate-500">{t('No contexts extracted yet.')}</p>
                          ) : (
                            <div className="mt-3 grid gap-3 sm:grid-cols-2">
                              {detail.contexts.map((context, index) => {
                                const versions = context.processor_versions as Record<string, unknown> | undefined;
                                const chunkValue = versions?.chunk_index;
                                const chunkIndex = typeof chunkValue === 'number' ? chunkValue + 1 : null;
                                return (
                                  <div key={`${context.context_type}-${index}`} className="rounded-2xl border border-slate-100 bg-white p-4">
                                    <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-slate-400">
                                      <span>{formatContextType(context.context_type)}</span>
                                      {chunkIndex ? (
                                        <span>{t('Chunk {index}', { index: chunkIndex })}</span>
                                      ) : null}
                                    </div>
                                    <p className="mt-2 text-sm font-semibold text-slate-900">{context.title}</p>
                                    <p className="mt-1 text-xs text-slate-600">{context.summary}</p>
                                    {context.keywords.length > 0 && (
                                      <div className="mt-3 flex flex-wrap gap-1">
                                        {context.keywords.map((keyword) => (
                                          <span key={keyword} className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">
                                            {keyword}
                                          </span>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>

                        {(detail.transcript_segments?.length || detail.transcript_text) && (
                          <div>
                            <h4 className="text-sm font-semibold text-slate-900">{t('Transcript')}</h4>
                            {detail.transcript_segments && detail.transcript_segments.length > 0 ? (
                              <div className="mt-3 space-y-3 rounded-2xl border border-slate-100 bg-white p-4 max-h-56 overflow-y-auto">
                                {detail.transcript_segments.map((segment, index) => (
                                  <div key={`${segment.start_ms}-${index}`} className="text-xs text-slate-600">
                                    <span className="text-[10px] uppercase tracking-wide text-slate-400">
                                      {formatDuration(segment.start_ms)} - {formatDuration(segment.end_ms)}
                                    </span>
                                    <p className="mt-1 text-slate-700">{segment.text || t('...')}</p>
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <p className="mt-2 whitespace-pre-wrap text-xs text-slate-600">{detail.transcript_text}</p>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </PageMotion>
  );
};
