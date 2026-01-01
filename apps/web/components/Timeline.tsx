import React, { useEffect, useMemo, useState } from 'react';
import { Calendar, ChevronLeft, ChevronRight, FileText, Image as ImageIcon, Mic, Play, Search, UploadCloud, Video, X } from 'lucide-react';
import { apiDelete, apiGet, apiPatch, apiPost } from '../services/api';
import {
  IngestResponse,
  SearchResponse,
  SearchResult,
  TimelineDay,
  TimelineEpisode,
  TimelineEpisodeDetail,
  TimelineItem,
  TimelineItemDetail,
  TimelineItemsResponse,
  TimelineFocus,
  TimelineViewMode,
  UploadUrlResponse,
} from '../types';

type ViewMode = TimelineViewMode;

const formatDate = (value?: string) => {
  if (!value) return 'Unknown date';
  return new Date(value).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
};

const formatMonthLabel = (value: Date) =>
  value.toLocaleDateString(undefined, { month: 'long' });

const formatDayLabel = (value: Date) =>
  value.toLocaleDateString(undefined, { weekday: 'short' });

const buildLabel = (item: TimelineItem) =>
  item.caption || item.original_filename || `${item.item_type} upload`;

const formatTime = (value?: string) => {
  if (!value) return 'Unknown time';
  return new Date(value).toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });
};

const formatTimeRange = (start?: string | null, end?: string | null) => {
  if (!start || !end) return 'Time window unknown';
  return `${formatTime(start)} - ${formatTime(end)}`;
};

const formatDuration = (ms: number) => {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
};

const formatClockTime = (value: Date) =>
  value.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

const buildDateWithTime = (value: Date, timeValue: string) => {
  const [hours, minutes] = timeValue.split(':').map((part) => Number(part));
  const next = new Date(value);
  next.setHours(Number.isFinite(hours) ? hours : 0, Number.isFinite(minutes) ? minutes : 0, 0, 0);
  return next;
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

const toDateKey = (value: Date) => {
  const year = value.getFullYear();
  const month = `${value.getMonth() + 1}`.padStart(2, '0');
  const day = `${value.getDate()}`.padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const startOfWeek = (value: Date) => {
  const date = new Date(value);
  const day = (date.getDay() + 6) % 7;
  date.setDate(date.getDate() - day);
  return date;
};

const endOfWeek = (value: Date) => {
  const start = startOfWeek(value);
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  return end;
};

const startOfMonth = (value: Date) => new Date(value.getFullYear(), value.getMonth(), 1);
const endOfMonth = (value: Date) => new Date(value.getFullYear(), value.getMonth() + 1, 0);

const startOfYear = (value: Date) => new Date(value.getFullYear(), 0, 1);
const endOfYear = (value: Date) => new Date(value.getFullYear(), 11, 31);

const formatRangeLabel = (view: ViewMode, anchor: Date) => {
  if (view === 'day') {
    return anchor.toLocaleDateString(undefined, {
      weekday: 'long',
      month: 'short',
      day: 'numeric',
    });
  }
  if (view === 'week') {
    const start = startOfWeek(anchor);
    const end = endOfWeek(anchor);
    return `${start.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} - ${end.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`;
  }
  if (view === 'month') {
    return anchor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  }
  if (view === 'all') {
    return 'All time';
  }
  return anchor.getFullYear().toString();
};

const buildDateRange = (start: Date, end: Date) => {
  const dates: Date[] = [];
  const cursor = new Date(start);
  while (cursor <= end) {
    dates.push(new Date(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return dates;
};

const buildMonthGrid = (anchor: Date) =>
  buildDateRange(startOfWeek(startOfMonth(anchor)), endOfWeek(endOfMonth(anchor)));

const isSameMonth = (value: Date, anchor: Date) =>
  value.getFullYear() === anchor.getFullYear() && value.getMonth() === anchor.getMonth();

const getThumbnail = (item: TimelineItem) => {
  if (item.item_type === 'video') return item.poster_url || null;
  if (item.item_type === 'photo') return item.download_url || null;
  return null;
};

const VIEW_LABELS: Record<ViewMode, string> = {
  day: 'Day',
  week: 'Week',
  month: 'Month',
  year: 'Year',
  all: 'All',
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
  const [days, setDays] = useState<TimelineDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('day');
  const [anchorDate, setAnchorDate] = useState<Date>(() => new Date());
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

  const range = useMemo(() => {
    if (viewMode === 'day') {
      return { start: anchorDate, end: anchorDate };
    }
    if (viewMode === 'all') {
      return { start: anchorDate, end: anchorDate };
    }
    if (viewMode === 'week') {
      return { start: startOfWeek(anchorDate), end: endOfWeek(anchorDate) };
    }
    if (viewMode === 'month') {
      return { start: startOfMonth(anchorDate), end: endOfMonth(anchorDate) };
    }
    return { start: startOfYear(anchorDate), end: endOfYear(anchorDate) };
  }, [viewMode, anchorDate]);

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
        const tzOffsetMinutes = range.start.getTimezoneOffset();
        const limit = viewMode === 'day' ? '1' : '600';
        const query = new URLSearchParams({
          start_date: toDateKey(range.start),
          end_date: toDateKey(range.end),
          limit,
          tz_offset_minutes: tzOffsetMinutes.toString(),
        });
        const data = await apiGet<TimelineDay[]>(`/timeline?${query.toString()}`);
        if (mounted) {
          setDays(data);
        }
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : 'Failed to load timeline.');
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
  }, [range, reloadKey, viewMode]);

  useEffect(() => {
    if (!focus) {
      return;
    }
    if (focus.viewMode) {
      setViewMode(focus.viewMode);
    }
    if (focus.anchorDate) {
      const parsed = new Date(focus.anchorDate);
      if (!Number.isNaN(parsed.getTime())) {
        setAnchorDate(parsed);
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
  }, [focus, onFocusHandled]);

  const dayLookup = useMemo(() => {
    const map = new Map<string, TimelineDay>();
    days.forEach((day) => {
      map.set(day.date, day);
    });
    return map;
  }, [days]);

  const dayKey = useMemo(() => toDateKey(anchorDate), [anchorDate]);
  const dayEpisodes = useMemo(() => {
    if (viewMode !== 'day') {
      return [];
    }
    return dayLookup.get(dayKey)?.episodes ?? [];
  }, [dayLookup, dayKey, viewMode]);

  const daySummary = useMemo(() => {
    if (viewMode !== 'day') {
      return null;
    }
    return dayLookup.get(dayKey)?.daily_summary ?? null;
  }, [dayLookup, dayKey, viewMode]);

  const loadDayItems = async (reset = false) => {
    if (dayLoading && !reset) {
      return;
    }
    setDayLoading(true);
    setDayError(null);
    try {
      const nextOffset = reset ? 0 : dayOffset;
      const tzOffsetMinutes = anchorDate.getTimezoneOffset();
      const query = new URLSearchParams({
        start_date: toDateKey(anchorDate),
        end_date: toDateKey(anchorDate),
        limit: DAY_PAGE_SIZE.toString(),
        offset: nextOffset.toString(),
        tz_offset_minutes: tzOffsetMinutes.toString(),
      });
      const data = await apiGet<TimelineItemsResponse>(`/timeline/items?${query.toString()}`);
      setDayItems((prev) => (reset ? data.items : [...prev, ...data.items]));
      setDayTotal(data.total);
      setDayOffset(nextOffset + data.items.length);
    } catch (err) {
      setDayError(err instanceof Error ? err.message : 'Failed to load memories.');
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
      setAllError(err instanceof Error ? err.message : 'Failed to load memories.');
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
    () => buildDateWithTime(anchorDate, uploadStartTime),
    [anchorDate, uploadStartTime]
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

  const visibleDayEpisodes = useMemo(
    () => sortedDayEpisodes.slice(0, episodeVisibleCount),
    [sortedDayEpisodes, episodeVisibleCount]
  );

  const hasMoreEpisodes = useMemo(
    () => sortedDayEpisodes.length > episodeVisibleCount,
    [sortedDayEpisodes.length, episodeVisibleCount]
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

  const memoryCount = useMemo(
    () => sortedDayEpisodes.length + sortedDayItems.length,
    [sortedDayEpisodes.length, sortedDayItems.length]
  );

  const hasItems = useMemo(() => days.some(day => day.items.length > 0), [days]);
  const rangeDates = useMemo(() => buildDateRange(range.start, range.end), [range]);
  const monthGrid = useMemo(() => buildMonthGrid(anchorDate), [anchorDate]);

  const rangeTotal = useMemo(
    () => days.reduce((sum, day) => sum + day.item_count, 0),
    [days]
  );

  const monthTotals = useMemo(() => {
    const totals = Array.from({ length: 12 }, () => 0);
    days.forEach((day) => {
      const parsed = new Date(`${day.date}T00:00:00`);
      if (!Number.isNaN(parsed.getTime())) {
        totals[parsed.getMonth()] += day.item_count;
      }
    });
    return totals;
  }, [days]);

  const maxMonthTotal = useMemo(
    () => Math.max(1, ...monthTotals),
    [monthTotals]
  );

  const weekdayLabels = useMemo(() => {
    const base = startOfWeek(new Date());
    return Array.from({ length: 7 }, (_, index) => {
      const date = new Date(base);
      date.setDate(base.getDate() + index);
      return formatDayLabel(date);
    });
  }, []);

  const removeItem = (itemId: string) => {
    setDays((prev) =>
      prev
        .map((day) => {
          const remaining = day.items.filter((item) => item.id !== itemId);
          return { ...day, items: remaining, item_count: remaining.length };
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
          setSearchError(err instanceof Error ? err.message : 'Search failed.');
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
    if (!confirm('Delete this memory? This will remove it from storage and search.')) {
      return;
    }
    setDeletingId(itemId);
    try {
      await apiDelete(`/timeline/items/${itemId}`);
      removeItem(itemId);
      setReloadKey((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete item.');
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
      setEpisodeEditError(err instanceof Error ? err.message : 'Failed to update episode.');
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
      setAnchorDate(eventTime);
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
    try {
      for (const [index, file] of uploadFiles.entries()) {
        const contentType = file.type || 'application/octet-stream';
        const uploadMeta = await apiPost<UploadUrlResponse>('/storage/upload-url', {
          filename: file.name,
          content_type: contentType,
          prefix: 'uploads/ui',
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
          client_tz_offset_minutes: new Date().getTimezoneOffset(),
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
          `Queued ${uploadFiles.length} upload(s) for ${formatDate(anchorDate.toISOString())} (${formatClockTime(uploadStart)} - ${formatClockTime(uploadEnd)}).`
        );
      } else {
        setUploadSuccess(`Queued ${uploadFiles.length} upload(s) using file timestamps.`);
      }
      setUploadFiles([]);
      if (newPending.length > 0) {
        setPendingUploadIds((prev) => [...prev, ...newPending]);
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed.');
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
          setUploadError('Uploads are still processing. Refresh later for updates.');
          setPendingUploadIds([]);
          return;
        }
      }
      if (!cancelled && attempts < 60) {
        timeoutId = window.setTimeout(poll, 5000);
      } else if (!cancelled) {
        setUploadError('Uploads are still processing. Refresh later for updates.');
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
    if (!sortedDayItems.length && !sortedDayEpisodes.length) {
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
    if (sortedDayEpisodes.length > 0) {
      setSelectedEpisodeId(sortedDayEpisodes[0].episode_id);
      setSelectedItemId(null);
      return;
    }
    if (sortedDayItems.length > 0) {
      setSelectedItemId(sortedDayItems[0].id);
      setSelectedEpisodeId(null);
    }
  }, [sortedDayItems, sortedDayEpisodes, viewMode, selectedItemId, selectedEpisodeId]);

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
          setDetailError(err instanceof Error ? err.message : 'Failed to load memory detail.');
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
          setEpisodeError(err instanceof Error ? err.message : 'Failed to load episode detail.');
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
    const next = new Date(anchorDate);
    if (viewMode === 'all') {
      return;
    }
    if (viewMode === 'day') {
      next.setDate(next.getDate() + direction);
    } else if (viewMode === 'week') {
      next.setDate(next.getDate() + direction * 7);
    } else if (viewMode === 'month') {
      next.setMonth(next.getMonth() + direction);
    } else {
      next.setFullYear(next.getFullYear() + direction);
    }
    setAnchorDate(next);
  };

  const handleToday = () => {
    setAnchorDate(new Date());
    setViewMode('day');
  };

  return (
    <div className="h-full overflow-y-auto p-4 md:p-8">
      <div className="relative overflow-hidden rounded-[32px] border border-white/70 bg-gradient-to-br from-slate-50 via-white to-slate-100 shadow-[0_40px_120px_-60px_rgba(15,23,42,0.45)]">
        <div className="absolute -top-32 -left-24 h-64 w-64 rounded-full bg-blue-200/40 blur-3xl" />
        <div className="absolute -bottom-32 -right-20 h-64 w-64 rounded-full bg-indigo-200/40 blur-3xl" />
        <div className="relative z-10 space-y-6 p-6 md:p-10">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h1 className="text-3xl font-semibold tracking-tight text-slate-900">Timeline</h1>
              <p className="text-sm text-slate-600">Move through your day, week, month, and year of memories.</p>
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
                    {VIEW_LABELS[mode]}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2 rounded-full border border-white/70 bg-white/70 px-3 py-1.5 text-xs shadow-sm backdrop-blur">
                <Search className="h-4 w-4 text-slate-400" />
                <input
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search memories"
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
                  {formatRangeLabel(viewMode, anchorDate)}
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
                  Today
                </button>
              </div>
            </div>
          </div>

          {searchQuery.trim() && (
            <div className="rounded-2xl border border-white/70 bg-white/80 p-4 shadow-sm backdrop-blur">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold text-slate-900">Search results</h2>
                  <p className="text-xs text-slate-500">
                    {searchLoading ? 'Searching...' : `${searchResults.length} result(s)`}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  className="text-xs font-semibold text-slate-500 hover:text-slate-700"
                >
                  Clear
                </button>
              </div>
              {searchError && <div className="mt-3 text-xs text-red-600">{searchError}</div>}
              {!searchLoading && !searchError && searchResults.length === 0 && (
                <div className="mt-3 text-xs text-slate-500">No matches found.</div>
              )}
              {searchResults.length > 0 && (
                <div className="mt-3 space-y-2">
                  {searchResults.map((result) => {
                    const isEpisode = result.payload && result.payload['is_episode'] === true;
                    const isDaily = result.context_type === 'daily_summary';
                    const label = isDaily ? 'Daily summary' : isEpisode ? 'Episode' : 'Memory';
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
                            <span>{result.context_type.replace(/_/g, ' ')}</span>
                          )}
                          {result.event_time_utc && (
                            <span>{formatDate(result.event_time_utc)}</span>
                          )}
                        </div>
                        <p className="mt-1 text-sm font-semibold text-slate-900">
                          {result.title || 'Untitled result'}
                        </p>
                        {result.summary && (
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
              Loading timeline...
            </div>
          )}

          {error && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {!loading && !error && !hasItems && viewMode !== 'all' && (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-white/70 px-6 py-14 text-center text-sm text-slate-500 shadow-sm backdrop-blur">
              No memories yet. Upload something to start your timeline.
            </div>
          )}

          {!loading && !error && viewMode !== 'day' && viewMode !== 'all' && hasItems && (
            <div className="rounded-2xl border border-white/70 bg-white/80 p-6 shadow-sm backdrop-blur">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-slate-900">
                    {VIEW_LABELS[viewMode]} overview
                  </h2>
                  <p className="text-xs text-slate-500">
                    {rangeDates.length} days, {rangeTotal} memories
                  </p>
                </div>
              </div>

              {viewMode === 'week' && (
                <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-7">
                  {rangeDates.map((date) => {
                    const key = toDateKey(date);
                    const day = dayLookup.get(key);
                    const count = day?.item_count ?? 0;
                    const preview = day?.items?.[0];
                    const thumbnail = preview ? getThumbnail(preview) : null;
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => {
                          setAnchorDate(date);
                          setViewMode('day');
                        }}
                        className={`rounded-2xl border p-4 text-left transition-all ${
                          count > 0
                            ? 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-md'
                            : 'border-white/60 bg-slate-50/70 text-slate-400'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-semibold uppercase tracking-wide">
                            {formatDayLabel(date)}
                          </span>
                          <span className="text-xs font-semibold text-slate-500">
                            {date.getDate()}
                          </span>
                        </div>
                        <div className="mt-4 flex items-center justify-between">
                          <span className="text-sm font-semibold">
                            {count > 0 ? `${count} memories` : 'No memories'}
                          </span>
                          {thumbnail ? (
                            <img
                              src={thumbnail}
                              alt="Preview"
                              className="h-8 w-8 rounded-full object-cover"
                            />
                          ) : (
                            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-100 text-slate-400">
                              {preview?.item_type === 'video' ? (
                                <Video className="h-4 w-4" />
                              ) : preview?.item_type === 'audio' ? (
                                <Mic className="h-4 w-4" />
                              ) : (
                                <ImageIcon className="h-4 w-4" />
                              )}
                            </div>
                          )}
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
                      const key = toDateKey(date);
                      const day = dayLookup.get(key);
                      const count = day?.item_count ?? 0;
                      const isCurrent = isSameMonth(date, anchorDate);
                      return (
                        <button
                          key={key}
                          type="button"
                          onClick={() => {
                            setAnchorDate(date);
                            setViewMode('day');
                          }}
                          className={`flex aspect-square flex-col items-center justify-center rounded-2xl border text-xs font-semibold transition-all ${
                            isCurrent
                              ? 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-md'
                              : 'border-white/60 bg-slate-50/70 text-slate-400'
                          }`}
                        >
                          <span className="text-sm">{date.getDate()}</span>
                          {count > 0 && (
                            <span className="text-[10px] text-slate-500">{count}</span>
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
                    return (
                      <button
                        key={index}
                        type="button"
                        onClick={() => {
                          setAnchorDate(new Date(anchorDate.getFullYear(), index, 1));
                          setViewMode('month');
                        }}
                        className={`rounded-2xl border p-4 text-left transition-all ${
                          total > 0
                            ? 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-md'
                            : 'border-white/60 bg-slate-50/70 text-slate-400'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-semibold text-slate-900">
                            {formatMonthLabel(new Date(anchorDate.getFullYear(), index, 1))}
                          </span>
                          <span className="text-xs font-semibold text-slate-500">
                            {total}
                          </span>
                        </div>
                        <div className="mt-4 h-2 w-full rounded-full bg-slate-100">
                          <div
                            className="h-full rounded-full bg-slate-900"
                            style={{ width: `${percent}%` }}
                          />
                        </div>
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
                  <h2 className="text-lg font-semibold text-slate-900">All memories</h2>
                  <p className="text-xs text-slate-500">{allTotal} total</p>
                </div>
                <div className="text-xs text-slate-400">Newest first</div>
              </div>
              {allError && <div className="mt-4 text-xs text-red-600">{allError}</div>}
              {allLoading && allItems.length === 0 && (
                <div className="mt-6 text-sm text-slate-500">Loading memories...</div>
              )}
              {!allLoading && allItems.length === 0 && (
                <div className="mt-6 text-sm text-slate-500">No memories yet.</div>
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
                              setAnchorDate(parsed);
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
                            <img src={thumbnail} alt={buildLabel(item)} className="h-full w-full object-cover" />
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
                            <span>{item.item_type}</span>
                            <span>{formatDate(item.captured_at)}</span>
                            {item.captured_at && <span>{formatTime(item.captured_at)}</span>}
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
                  {allLoading ? 'Loading more...' : 'Load more memories'}
                </button>
              )}
            </div>
          )}

          {!loading && !error && viewMode === 'day' && (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-[340px_1fr]">
              <div className="rounded-2xl border border-white/70 bg-white/80 p-4 shadow-sm backdrop-blur">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-900">Daily timeline</h2>
                    <p className="text-xs text-slate-500">
                      {memoryCount} memories
                      {dayTotal > 0 && dayItems.length < dayTotal
                        ? ` · Showing ${dayItems.length} of ${dayTotal} items`
                        : ''}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="flex flex-wrap gap-1 text-[10px]">
                      {dayStats.photo > 0 && (
                        <span className="rounded-full bg-slate-900 px-2 py-0.5 text-white">
                          {dayStats.photo} photos
                        </span>
                      )}
                      {dayStats.video > 0 && (
                        <span className="rounded-full bg-white px-2 py-0.5 text-slate-700">
                          {dayStats.video} videos
                        </span>
                      )}
                      {dayStats.audio > 0 && (
                        <span className="rounded-full bg-white px-2 py-0.5 text-slate-700">
                          {dayStats.audio} audio
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => setUploadOpen((open) => !open)}
                      className="flex items-center gap-1 rounded-full border border-slate-200 bg-white px-3 py-1 text-[10px] font-semibold text-slate-700 hover:border-slate-300"
                    >
                      <UploadCloud className="h-3.5 w-3.5" />
                      {uploadOpen ? 'Close uploader' : 'Upload for this day'}
                    </button>
                  </div>
                </div>

                {uploadOpen && (
                  <div className="mt-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-sm font-semibold text-slate-900">
                          Upload memories for {formatDate(anchorDate.toISOString())}
                        </h3>
                        <p className="text-xs text-slate-500">
                          Choose how we should timestamp the uploads for this day.
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
                          Use file time
                        </button>
                        <button
                          type="button"
                          onClick={() => setTimeMode('window')}
                          className={`rounded-full px-3 py-1 transition-colors ${
                            timeMode === 'window' ? 'bg-slate-900 text-white' : 'hover:bg-white'
                          }`}
                        >
                          Set time window
                        </button>
                      </div>
                      {timeMode === 'file' ? (
                        <p className="text-xs text-slate-500">
                          We use the file timestamp when available; otherwise the upload time is used.
                        </p>
                      ) : (
                        <div className="grid gap-3 sm:grid-cols-[minmax(0,180px)_minmax(0,180px)_minmax(0,1fr)]">
                          <label className="text-xs text-slate-500">
                            Start time
                            <input
                              type="time"
                              value={uploadStartTime}
                              onChange={(event) => setUploadStartTime(event.target.value)}
                              className="mt-1 h-10 w-full min-w-[140px] rounded-lg border border-slate-200 px-3 text-sm text-slate-700"
                            />
                          </label>
                          <label className="text-xs text-slate-500">
                            Duration (hours)
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
                            Window: {formatClockTime(uploadStart)} - {formatClockTime(uploadEnd)}
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="mt-4 flex flex-col gap-3">
                      <label className="flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-3 text-xs font-semibold text-slate-600 hover:border-slate-300 hover:bg-slate-100">
                        <UploadCloud className="h-4 w-4" />
                        Select files
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
                              Clear files
                            </button>
                            <button
                              type="button"
                              onClick={handleManualUpload}
                              disabled={uploading}
                              className="rounded-full bg-slate-900 px-4 py-1.5 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
                            >
                              {uploading
                                ? `Uploading ${uploadedCount}/${uploadFiles.length}`
                                : timeMode === 'window'
                                  ? 'Upload to this time window'
                                  : 'Upload with file timestamps'}
                            </button>
                          </div>
                        </div>
                      )}
                      {uploadError && <div className="text-xs text-red-600">{uploadError}</div>}
                      {uploadSuccess && <div className="text-xs text-green-600">{uploadSuccess}</div>}
                    </div>
                  </div>
                )}

                {sortedDayEpisodes.length > 0 && (
                  <div className="mt-4 space-y-3">
                    <div className="flex items-center justify-between text-xs text-slate-500">
                      <span className="font-semibold text-slate-700">Merged episodes</span>
                      <span>{sortedDayEpisodes.length}</span>
                    </div>
                    {visibleDayEpisodes.map((episode) => {
                      const isActive = episode.episode_id === selectedEpisodeId;
                      const preview = getEpisodePreview(episode);
                      return (
                        <button
                          key={episode.episode_id}
                          type="button"
                          onClick={() => {
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
                                <img src={preview} alt={episode.title} className="h-full w-full object-cover" />
                              ) : (
                                <div className="flex h-full w-full items-center justify-center text-slate-400">
                                  <ImageIcon className="h-4 w-4" />
                                </div>
                              )}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between text-[10px] uppercase tracking-wide">
                                <span className={isActive ? 'text-white/70' : 'text-slate-400'}>
                                  Episode
                                </span>
                                <span className={isActive ? 'text-white/70' : 'text-slate-400'}>
                                  {episode.item_count} items
                                </span>
                              </div>
                              <p className={`mt-1 text-sm font-semibold ${isActive ? 'text-white' : 'text-slate-900'}`}>
                                {episode.title}
                              </p>
                              <p className={`mt-1 text-xs ${isActive ? 'text-white/70' : 'text-slate-500'}`}>
                                {formatTimeRange(episode.start_time_utc, episode.end_time_utc)}
                              </p>
                              <p className={`mt-1 line-clamp-2 text-xs ${isActive ? 'text-white/70' : 'text-slate-500'}`}>
                                {episode.summary}
                              </p>
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
                        Show more episodes
                      </button>
                    )}
                  </div>
                )}

                {dayError && (
                  <div className="mt-3 text-xs text-red-600">{dayError}</div>
                )}

                {dayLoading && dayItems.length === 0 && sortedDayEpisodes.length === 0 ? (
                  <div className="py-8 text-center text-sm text-slate-500">
                    Loading memories...
                  </div>
                ) : sortedDayItems.length === 0 && sortedDayEpisodes.length === 0 ? (
                  <div className="py-8 text-center text-sm text-slate-500">
                    No memories for this day.
                  </div>
                ) : sortedDayItems.length === 0 ? (
                  <div className="py-4 text-center text-xs text-slate-500">
                    No individual memories yet.
                  </div>
                ) : (
                  <div className="mt-4 space-y-4">
                    {sortedDayItems.map((item, index) => {
                      const isActive = item.id === selectedItemId;
                      const thumbnail = getThumbnail(item);
                      const showConnector = index < sortedDayItems.length - 1;
                      return (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => {
                            setSelectedItemId(item.id);
                            setSelectedEpisodeId(null);
                          }}
                          className="w-full text-left"
                        >
                          <div className="flex gap-3">
                            <div className="flex flex-col items-center pt-1">
                              <span className={`text-[10px] font-semibold ${isActive ? 'text-slate-900' : 'text-slate-400'}`}>
                                {formatTime(item.captured_at)}
                              </span>
                              <span className={`mt-1 h-2.5 w-2.5 rounded-full ${isActive ? 'bg-slate-900' : 'bg-slate-300'}`} />
                              {showConnector && <span className="mt-1 h-10 w-px bg-slate-200" />}
                            </div>
                            <div
                              className={`flex flex-1 items-center gap-3 rounded-2xl border p-3 transition-all ${
                                isActive
                                  ? 'border-slate-900 bg-slate-900 text-white shadow-lg'
                                  : 'border-white/60 bg-white/90 hover:border-slate-200 hover:shadow'
                              }`}
                            >
                              <div className="relative h-14 w-14 flex-shrink-0 overflow-hidden rounded-xl bg-slate-100">
                                {thumbnail ? (
                                  <img src={thumbnail} alt={buildLabel(item)} className="h-full w-full object-cover" />
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
                                <div className="flex items-center justify-between text-[10px] uppercase tracking-wide">
                                  <span className={`${isActive ? 'text-white/80' : 'text-slate-400'}`}>
                                    {item.item_type}
                                  </span>
                                  <span className={`${isActive ? 'text-white/70' : 'text-slate-400'}`}>
                                    {item.processed ? 'Processed' : 'Processing'}
                                  </span>
                                </div>
                                <p className={`mt-1 line-clamp-2 text-sm font-semibold ${isActive ? 'text-white' : 'text-slate-900'}`}>
                                  {buildLabel(item)}
                                </p>
                              </div>
                            </div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                )}
                {hasMoreDayItems && (
                  <button
                    type="button"
                    onClick={() => loadDayItems(false)}
                    disabled={dayLoading}
                    className="mt-4 w-full rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                  >
                    {dayLoading ? 'Loading more...' : 'Load more memories'}
                  </button>
                )}
              </div>

              <div className="rounded-2xl border border-white/70 bg-white/80 p-6 shadow-sm backdrop-blur">
                {viewMode === 'day' && daySummary && (
                  <div className="mb-6 rounded-2xl border border-slate-100 bg-white p-4">
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                      <FileText className="h-3 w-3" />
                      Daily summary
                    </div>
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
                  </div>
                )}
                {selectedEpisodeId ? (
                  <>
                    {episodeLoading && (
                      <div className="text-sm text-slate-500">Loading episode details...</div>
                    )}
                    {episodeError && (
                      <div className="text-sm text-red-600">{episodeError}</div>
                    )}
                    {!episodeLoading && !episodeDetail && (
                      <div className="text-sm text-slate-500">Select an episode to see details.</div>
                    )}
                    {episodeDetail && (
                      <div className="space-y-6">
                        <div>
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                              <h3 className="text-lg font-semibold text-slate-900">{episodeDetail.title}</h3>
                              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                                <span>{formatDate(episodeDetail.start_time_utc || undefined)}</span>
                                <span className="rounded-full bg-slate-900 px-2 py-0.5 text-white">
                                  Episode
                                </span>
                                <span className="rounded-full bg-white px-2 py-0.5 text-slate-600">
                                  {episodeDetail.source_item_ids.length} items
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
                              {episodeEditOpen ? 'Editing' : 'Edit episode'}
                            </button>
                          </div>
                          <p className="mt-2 text-sm text-slate-600">
                            {formatTimeRange(episodeDetail.start_time_utc, episodeDetail.end_time_utc)}
                          </p>
                          {episodeEditOpen ? (
                            <div className="mt-4 space-y-3 rounded-2xl border border-slate-100 bg-white p-4">
                              <label className="text-xs text-slate-500">
                                Title
                                <input
                                  value={episodeEditTitle}
                                  onChange={(event) => setEpisodeEditTitle(event.target.value)}
                                  className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-700"
                                />
                              </label>
                              <label className="text-xs text-slate-500">
                                Summary
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
                                  {episodeEditSaving ? 'Saving...' : 'Save changes'}
                                </button>
                                <button
                                  type="button"
                                  onClick={handleEpisodeCancel}
                                  className="rounded-full border border-slate-200 px-4 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
                                >
                                  Cancel
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
                          <h4 className="text-sm font-semibold text-slate-900">Episode contexts</h4>
                          <div className="mt-3 grid gap-3 sm:grid-cols-2">
                            {episodeDetail.contexts.map((context, index) => (
                              <div key={`${context.context_type}-${index}`} className="rounded-2xl border border-slate-100 bg-white p-4">
                                <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-slate-400">
                                  <span>{context.context_type.replace(/_/g, ' ')}</span>
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
                          <h4 className="text-sm font-semibold text-slate-900">Episode items</h4>
                          <div className="mt-3 grid gap-3 sm:grid-cols-2">
                            {episodeDetail.items.map((item) => (
                              <button
                                key={item.id}
                                type="button"
                                onClick={() => {
                                  setSelectedEpisodeId(null);
                                  setSelectedItemId(item.id);
                                }}
                                className="flex items-center gap-3 rounded-2xl border border-slate-100 bg-white p-3 text-left hover:shadow"
                              >
                                <div className="h-12 w-12 overflow-hidden rounded-xl bg-slate-100">
                                  {item.item_type === 'video' && item.poster_url ? (
                                    <img src={item.poster_url} alt={buildLabel(item)} className="h-full w-full object-cover" />
                                  ) : item.item_type === 'photo' && item.download_url ? (
                                    <img src={item.download_url} alt={buildLabel(item)} className="h-full w-full object-cover" />
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
                                  <p className="text-xs uppercase tracking-wide text-slate-400">{item.item_type}</p>
                                  <p className="mt-1 text-sm font-semibold text-slate-900 line-clamp-2">
                                    {buildLabel(item)}
                                  </p>
                                </div>
                              </button>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    {detailLoading && (
                      <div className="text-sm text-slate-500">Loading memory details...</div>
                    )}
                    {detailError && (
                      <div className="text-sm text-red-600">{detailError}</div>
                    )}
                    {!detailLoading && !detail && (
                      <div className="text-sm text-slate-500">Select a memory to see details.</div>
                    )}
                    {detail && (
                      <div className="space-y-6">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <div>
                            <h3 className="text-lg font-semibold text-slate-900">{buildLabel(detail)}</h3>
                            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                              <span>{formatDate(detail.captured_at)}</span>
                              <span className="rounded-full bg-slate-900 px-2 py-0.5 text-white">
                                {detail.item_type}
                              </span>
                              <span className="rounded-full bg-white px-2 py-0.5 text-slate-600">
                                {detail.processed ? 'Processed' : 'Processing'}
                              </span>
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={() => handleDelete(detail.id)}
                            className="rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
                            disabled={deletingId === detail.id}
                          >
                            {deletingId === detail.id ? 'Deleting...' : 'Delete'}
                          </button>
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
                              <div className="p-6 text-slate-400">Video unavailable.</div>
                            )
                          ) : detail.item_type === 'audio' ? (
                            detail.download_url ? (
                              <div className="bg-white p-4">
                                <audio src={detail.download_url} controls className="w-full" preload="metadata" />
                              </div>
                            ) : (
                              <div className="p-6 text-slate-400">Audio unavailable.</div>
                            )
                          ) : detail.download_url ? (
                            <img src={detail.download_url} alt={buildLabel(detail)} className="w-full object-cover" />
                          ) : (
                            <div className="p-6 text-slate-400">Preview unavailable.</div>
                          )}
                        </div>

                        <div>
                          <h4 className="text-sm font-semibold text-slate-900">Contexts</h4>
                          {detail.contexts.length === 0 ? (
                            <p className="mt-2 text-xs text-slate-500">No contexts extracted yet.</p>
                          ) : (
                            <div className="mt-3 grid gap-3 sm:grid-cols-2">
                              {detail.contexts.map((context, index) => {
                                const versions = context.processor_versions as Record<string, unknown> | undefined;
                                const chunkValue = versions?.chunk_index;
                                const chunkIndex = typeof chunkValue === 'number' ? chunkValue + 1 : null;
                                return (
                                  <div key={`${context.context_type}-${index}`} className="rounded-2xl border border-slate-100 bg-white p-4">
                                    <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-slate-400">
                                      <span>{context.context_type.replace(/_/g, ' ')}</span>
                                      {chunkIndex ? <span>Chunk {chunkIndex}</span> : null}
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
                            <h4 className="text-sm font-semibold text-slate-900">Transcript</h4>
                            {detail.transcript_segments && detail.transcript_segments.length > 0 ? (
                              <div className="mt-3 space-y-3 rounded-2xl border border-slate-100 bg-white p-4 max-h-56 overflow-y-auto">
                                {detail.transcript_segments.map((segment, index) => (
                                  <div key={`${segment.start_ms}-${index}`} className="text-xs text-slate-600">
                                    <span className="text-[10px] uppercase tracking-wide text-slate-400">
                                      {formatDuration(segment.start_ms)} - {formatDuration(segment.end_ms)}
                                    </span>
                                    <p className="mt-1 text-slate-700">{segment.text || '...'}</p>
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
    </div>
  );
};
