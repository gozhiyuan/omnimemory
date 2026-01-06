import React, { useEffect, useState } from 'react';
import { UploadCloud, CheckCircle2, FileImage, X, AlertCircle, ChevronDown, FileText, Mic, Play, Video } from 'lucide-react';
import { apiGet, apiPost } from '../services/api';
import { PageMotion } from './PageMotion';
import { useSettings } from '../contexts/SettingsContext';
import { useI18n } from '../i18n/useI18n';
import { formatDateKey, getTimeZoneOffsetMinutes } from '../utils/time';
import {
  GooglePhotosAuthUrlResponse,
  GooglePhotosPickerSessionResponse,
  GooglePhotosStatus,
  GooglePhotosSyncRequest,
  GooglePhotosSyncResponse,
  IngestResponse,
  GooglePhotosPickerItem,
  GooglePhotosPickerItemsResponse,
  TimelineItem,
  TimelineItemsResponse,
  UploadUrlResponse,
} from '../types';

export const UploadManager: React.FC = () => {
  const RECENT_PAGE_SIZE = 6;
  const { settings } = useSettings();
  const { t, locale } = useI18n();
  const timeZone = settings.preferences.timezone;

  const [dragActive, setDragActive] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadedCount, setUploadedCount] = useState(0);
  const [googleStatus, setGoogleStatus] = useState<GooglePhotosStatus | null>(null);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [googleError, setGoogleError] = useState<string | null>(null);
  const [pickerLoading, setPickerLoading] = useState(false);
  const [pickerError, setPickerError] = useState<string | null>(null);
  const [pickerSessionId, setPickerSessionId] = useState<string | null>(null);
  const [pickerUri, setPickerUri] = useState<string | null>(null);
  const [pickerPollCycle, setPickerPollCycle] = useState(0);
  const [pickerPollExhausted, setPickerPollExhausted] = useState(false);
  const [syncLoading, setSyncLoading] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [recentItems, setRecentItems] = useState<TimelineItem[]>([]);
  const [recentLoading, setRecentLoading] = useState(false);
  const [recentError, setRecentError] = useState<string | null>(null);
  const [recentOffset, setRecentOffset] = useState(0);
  const [recentTotal, setRecentTotal] = useState(0);
  const [selectedItems, setSelectedItems] = useState<GooglePhotosPickerItem[]>([]);
  const [selectedLoading, setSelectedLoading] = useState(false);
  const [selectedError, setSelectedError] = useState<string | null>(null);
  const [selectedHint, setSelectedHint] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(true);

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

  const formatDateTime = (value?: string | Date | null) => {
    if (!value) {
      return t('Unknown date');
    }
    const parsed = typeof value === 'string' ? new Date(value) : value;
    if (Number.isNaN(parsed.getTime())) {
      return t('Unknown date');
    }
    return new Intl.DateTimeFormat(locale, {
      timeZone,
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    }).format(parsed);
  };

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFiles(e.dataTransfer.files);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault();
    if (e.target.files && e.target.files.length > 0) {
      handleFiles(e.target.files);
    }
  };

  const handleFiles = (fileList: FileList) => {
    const newFiles = Array.from(fileList);
    setFiles(prev => [...prev, ...newFiles]);
    setSuccess(false);
    setError(null);
    setUploadedCount(0);
  };

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index));
    setSuccess(false);
  };

  const clearFiles = () => {
    setFiles([]);
    setSuccess(false);
    setError(null);
    setUploadedCount(0);
  };

  const handleUpload = async () => {
    if (files.length === 0) return;
    setUploading(true);
    setError(null);
    setSuccess(false);
    setUploadedCount(0);

    try {
      for (const file of files) {
        const contentType = file.type || 'application/octet-stream';
        const uploadDate = file.lastModified ? new Date(file.lastModified) : new Date();
        const uploadMeta = await apiPost<UploadUrlResponse>('/storage/upload-url', {
          filename: file.name,
          content_type: contentType,
          prefix: 'uploads/ui',
          path_date: formatDateKey(uploadDate, timeZone),
        });
        if (!uploadMeta.url) {
          throw new Error(t('Upload URL missing for {name}', { name: file.name }));
        }

        const headers = { ...(uploadMeta.headers || {}), 'Content-Type': contentType };
        const uploadResponse = await fetch(uploadMeta.url, {
          method: 'PUT',
          headers,
          body: file,
        });
        if (!uploadResponse.ok) {
          const responseText = await uploadResponse.text();
          const details = responseText || '';
          throw new Error(
            t('Upload failed for {name}: {status} {details}', {
              name: file.name,
              status: uploadResponse.status,
              details,
            }).trim()
          );
        }

        const durationSec = await getMediaDuration(file);
        await apiPost<IngestResponse>('/upload/ingest', {
          storage_key: uploadMeta.key,
          item_type: inferItemType(file),
          content_type: contentType,
          original_filename: file.name,
          size_bytes: file.size,
          duration_sec: durationSec,
          client_tz_offset_minutes: getTimeZoneOffsetMinutes(new Date(), timeZone),
        });

        setUploadedCount((count) => count + 1);
      }

      setSuccess(true);
      setFiles([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Upload failed.'));
    } finally {
      setUploading(false);
    }
  };

  const loadGoogleStatus = async () => {
    setGoogleLoading(true);
    setGoogleError(null);
    try {
      const status = await apiGet<GooglePhotosStatus>('/integrations/google/photos/status');
      setGoogleStatus(status);
    } catch (err) {
      setGoogleError(
        err instanceof Error ? err.message : t('Failed to load Google Photos status.')
      );
    } finally {
      setGoogleLoading(false);
    }
  };

  const loadRecentItems = async (reset = false) => {
    if (recentLoading) {
      return;
    }
    setRecentError(null);
    setRecentLoading(true);
    try {
      const nextOffset = reset ? 0 : recentOffset;
      const query = new URLSearchParams({
        provider: 'google_photos',
        limit: RECENT_PAGE_SIZE.toString(),
        offset: nextOffset.toString(),
      });
      const data = await apiGet<TimelineItemsResponse>(`/timeline/items?${query.toString()}`);
      setRecentItems((prev) => (reset ? data.items : [...prev, ...data.items]));
      setRecentTotal(data.total);
      setRecentOffset(nextOffset + data.items.length);
    } catch (err) {
      setRecentError(err instanceof Error ? err.message : t('Failed to load recent items.'));
    } finally {
      setRecentLoading(false);
    }
  };

  const handleGoogleConnect = async () => {
    setGoogleLoading(true);
    setGoogleError(null);
    try {
      const response = await apiGet<GooglePhotosAuthUrlResponse>('/integrations/google/photos/auth-url');
      window.location.href = response.auth_url;
    } catch (err) {
      setGoogleError(
        err instanceof Error ? err.message : t('Failed to start Google Photos connection.')
      );
      setGoogleLoading(false);
    }
  };

  const hasMoreRecent = recentItems.length < recentTotal;

  const handleGooglePicker = async () => {
    setPickerLoading(true);
    setPickerError(null);
    setSyncMessage(null);
    setSelectedItems([]);
    setSelectedError(null);
    setSelectedHint(null);
    setPickerPollExhausted(false);
    try {
      const response = await apiPost<GooglePhotosPickerSessionResponse>('/integrations/google/photos/picker-session');
      setPickerSessionId(response.session_id);
      setPickerUri(response.picker_uri);
      window.open(response.picker_uri, '_blank', 'noopener,noreferrer');
      setPickerPollCycle((cycle) => cycle + 1);
    } catch (err) {
      setPickerError(err instanceof Error ? err.message : t('Failed to open Google Photos picker.'));
    } finally {
      setPickerLoading(false);
    }
  };

  const fetchPickerSelection = async () => {
    if (!pickerSessionId) {
      setSelectedError(t('Start a picker session before loading selections.'));
      return { count: 0, pending: false };
    }
    setSelectedError(null);
    setSelectedHint(null);
    try {
      const response = await apiGet<GooglePhotosPickerItemsResponse>(
        `/integrations/google/photos/picker-items?session_id=${encodeURIComponent(pickerSessionId)}`
      );
      if (response.status === 'pending') {
        setSelectedItems([]);
        setSelectedHint(
          response.message || t('Waiting for Google Photos selection to complete.')
        );
        return { count: 0, pending: true };
      }
      setSelectedHint(null);
      setSelectedItems(response.items);
      return { count: response.items.length, pending: false };
    } catch (err) {
      setSelectedError(err instanceof Error ? err.message : t('Failed to load picker selections.'));
      return { count: 0, pending: false };
    }
  };

  const handlePickerCheckAgain = () => {
    if (!pickerSessionId) {
      setSelectedError(t('Start a picker session before loading selections.'));
      return;
    }
    setPickerPollExhausted(false);
    setSelectedHint(t('Checking Google Photos selection...'));
    setPickerPollCycle((cycle) => cycle + 1);
  };

  const handlePickerReopen = () => {
    if (!pickerUri) {
      setPickerError(t('No picker session available. Start a new selection.'));
      return;
    }
    window.open(pickerUri, '_blank', 'noopener,noreferrer');
  };

  const handleGoogleSync = async () => {
    if (!pickerSessionId) {
      setPickerError(t('Start a picker session before syncing.'));
      return;
    }
    setSyncLoading(true);
    setSyncMessage(null);
    setPickerError(null);
    try {
      const payload: GooglePhotosSyncRequest = { session_id: pickerSessionId };
      const response = await apiPost<GooglePhotosSyncResponse>('/integrations/google/photos/sync', payload);
      setSyncMessage(t('Sync queued (task {task_id}).', { task_id: response.task_id }));
      void loadRecentItems(true);
    } catch (err) {
      setPickerError(err instanceof Error ? err.message : t('Failed to queue Google Photos sync.'));
    } finally {
      setSyncLoading(false);
    }
  };

  useEffect(() => {
    void loadGoogleStatus();
    void loadRecentItems(true);
  }, []);

  useEffect(() => {
    if (!pickerSessionId) return;

    const initialDelayMs = 2000;
    const maxDelayMs = 15000;
    const maxDurationMs = 90000;
    const backoffFactor = 1.6;

    let cancelled = false;
    let timeoutId: number | undefined;
    let delayMs = initialDelayMs;
    const startedAt = Date.now();

    setSelectedItems([]);
    setSelectedLoading(true);
    setSelectedError(null);
    setPickerPollExhausted(false);

    const pollSelection = async () => {
      const { count, pending } = await fetchPickerSelection();
      if (cancelled) return;
      if (!pending) {
        setSelectedLoading(false);
        setPickerPollExhausted(false);
        if (count === 0) {
          setSelectedHint(t('No items selected yet. Reopen the picker to choose photos.'));
        }
        return;
      }

      const elapsedMs = Date.now() - startedAt;
      if (elapsedMs >= maxDurationMs) {
        setSelectedLoading(false);
        setPickerPollExhausted(true);
        setSelectedHint(t('Still waiting on Google Photos. Check again or reopen the picker.'));
        return;
      }

      delayMs = Math.min(Math.floor(delayMs * backoffFactor), maxDelayMs);
      timeoutId = window.setTimeout(pollSelection, delayMs);
    };

    void pollSelection();

    return () => {
      cancelled = true;
      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }
      setSelectedLoading(false);
    };
  }, [pickerSessionId, pickerPollCycle]);

  const formatGoogleStatus = () => {
    if (!googleStatus?.connected) {
      return t('Not connected');
    }
    if (googleStatus.connected_at) {
      const connectedAt = new Date(googleStatus.connected_at);
      return t('Connected {time}', { time: formatDateTime(connectedAt) });
    }
    return t('Connected');
  };

  return (
    <PageMotion className="p-8 max-w-4xl mx-auto">
       <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900">{t('Ingestion Pipeline')}</h1>
        <p className="text-slate-500 mt-1">
          {t('Upload photos, videos, or connect external accounts.')}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        {/* Manual Upload Area */}
        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-slate-800">{t('Manual Upload')}</h2>
          <div 
            className={`relative border-2 border-dashed rounded-xl p-8 flex flex-col items-center justify-center text-center transition-colors min-h-[300px] ${
              dragActive ? 'border-primary-500 bg-primary-50' : 'border-slate-300 bg-slate-50'
            }`}
            onDragEnter={handleDrag}
            onDragLeave={handleDrag}
            onDragOver={handleDrag}
            onDrop={handleDrop}
          >
            <input 
              type="file" 
              multiple 
              className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
              onChange={handleChange}
              accept="image/*,video/*,audio/*"
            />
            <div className="p-4 bg-white rounded-full shadow-sm mb-4 pointer-events-none">
              <UploadCloud className={`w-8 h-8 ${dragActive ? 'text-primary-600' : 'text-slate-400'}`} />
            </div>
            <p className="text-sm font-medium text-slate-900 pointer-events-none">
              {t('Click to upload or drag and drop')}
            </p>
            <p className="text-xs text-slate-500 mt-2 max-w-xs pointer-events-none">
              {t('Supported: JPG, PNG, MP4, MOV, MP3, WAV (Max 5GB per batch)')}
            </p>
          </div>

          {files.length > 0 && (
            <div className="bg-white rounded-lg border border-slate-200 p-4 shadow-sm animate-fade-in">
              <div className="flex justify-between items-center mb-3">
                <span className="text-sm font-medium text-slate-700">
                  {t('{count} files selected', { count: files.length })}
                </span>
                <button 
                  onClick={clearFiles}
                  className="text-xs text-red-500 hover:text-red-600"
                >
                  {t('Clear all')}
                </button>
              </div>
              <div className="max-h-40 overflow-y-auto space-y-2 pr-1 custom-scrollbar">
                {files.map((file, idx) => (
                  <div key={idx} className="flex items-center justify-between text-xs p-2 bg-slate-50 rounded">
                    <div className="flex items-center truncate">
                      <FileImage className="w-4 h-4 text-slate-400 mr-2" />
                      <span className="truncate max-w-[150px]">{file.name}</span>
                    </div>
                    <button onClick={() => removeFile(idx)} className="text-slate-400 hover:text-slate-600">
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
              <button 
                onClick={handleUpload}
                disabled={uploading}
                className="w-full mt-4 bg-primary-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-primary-700 transition-colors disabled:opacity-50 flex items-center justify-center"
              >
                {uploading ? (
                  <>
                    <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    {t('Uploading {current}/{total}', {
                      current: uploadedCount,
                      total: files.length,
                    })}
                  </>
                ) : t('Start Processing')}
              </button>
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-center">
              <AlertCircle className="w-5 h-5 mr-2" />
              <span className="text-sm">{error}</span>
            </div>
          )}

          {success && (
             <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-lg flex items-center">
               <CheckCircle2 className="w-5 h-5 mr-2" />
               <span className="text-sm">{t('Batch uploaded & queued for processing!')}</span>
             </div>
          )}
        </div>

        {/* Integration Cards */}
        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-slate-800">{t('Connected Sources')}</h2>
          
          <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">
                   <img
                     src="https://upload.wikimedia.org/wikipedia/commons/thumb/c/c1/Google_%22G%22_logo.svg/768px-Google_%22G%22_logo.svg.png"
                     className="w-5 h-5"
                     alt="Google"
                     loading="lazy"
                   />
                </div>
                <div>
                  <h3 className="text-sm font-medium text-slate-900">{t('Google Photos')}</h3>
                  <p className={`text-xs flex items-center ${googleStatus?.connected ? 'text-green-600' : 'text-slate-500'}`}>
                    {googleStatus?.connected ? (
                      <CheckCircle2 size={12} className="mr-1" />
                    ) : (
                      <AlertCircle size={12} className="mr-1" />
                    )}
                    {googleLoading ? t('Checking status...') : formatGoogleStatus()}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  className="text-xs border border-slate-200 px-3 py-1.5 rounded-md hover:bg-slate-50 disabled:opacity-50"
                  onClick={handleGoogleConnect}
                  disabled={googleLoading}
                  type="button"
                >
                  {googleStatus?.connected ? t('Reconnect') : t('Connect')}
                </button>
                <button
                  className="text-xs bg-primary-600 text-white px-3 py-1.5 rounded-md hover:bg-primary-700 disabled:opacity-50"
                  onClick={handleGooglePicker}
                  disabled={!googleStatus?.connected || pickerLoading}
                  type="button"
                >
                  {pickerLoading ? t('Opening...') : t('Select photos')}
                </button>
              </div>
            </div>

            {googleError && (
              <div className="text-xs text-red-600 mt-2">{googleError}</div>
            )}
            {pickerError && (
              <div className="text-xs text-red-600 mt-2">{pickerError}</div>
            )}

            {googleStatus?.connected && (
              <div className="mt-4 border border-slate-200 rounded-lg">
                <button
                  type="button"
                  onClick={() => setDetailsOpen((open) => !open)}
                  className="w-full flex items-center justify-between px-3 py-2 text-sm font-medium text-slate-800"
                  aria-expanded={detailsOpen}
                >
                  <span>{t('Google Photos ingestion details')}</span>
                  <ChevronDown
                    className={`w-4 h-4 transition-transform ${detailsOpen ? 'rotate-180' : ''}`}
                  />
                </button>
                {detailsOpen && (
                  <div className="border-t border-slate-200 bg-slate-50 rounded-b-lg p-3 text-xs text-slate-600 space-y-3">
                    <div className="space-y-1">
                      <p className="text-sm text-slate-800 font-medium">
                        {t('Select photos in Google Picker')}
                      </p>
                      <p>
                        {t(
                          'Selections refresh automatically after you finish picking. Already ingested items are skipped during sync.'
                        )}
                      </p>
                      <div className="flex items-center gap-3">
                        <span className="text-slate-700 font-medium">
                          {selectedLoading
                            ? t('Loading selection...')
                            : t('Selected {count} photos', { count: selectedItems.length })}
                        </span>
                        {!pickerSessionId && (
                          <span className="text-slate-500">{t('Open the picker to start a selection.')}</span>
                        )}
                      </div>
                      {selectedError && <p className="text-xs text-red-600">{selectedError}</p>}
                      {selectedHint && !selectedError && (
                        <p className="text-xs text-slate-500">{selectedHint}</p>
                      )}
                      {pickerPollExhausted && !selectedError && (
                        <div className="flex flex-wrap items-center gap-2">
                          <button
                            className="text-xs border border-slate-300 px-2.5 py-1 rounded-md hover:bg-white disabled:opacity-50"
                            onClick={handlePickerCheckAgain}
                            disabled={selectedLoading}
                            type="button"
                          >
                            {t('Check again')}
                          </button>
                          <button
                            className="text-xs text-slate-600 hover:text-slate-800 underline underline-offset-2"
                            onClick={handlePickerReopen}
                            type="button"
                          >
                            {t('Reopen picker')}
                          </button>
                        </div>
                      )}
                      <button
                        className="text-xs bg-slate-900 text-white px-3 py-1.5 rounded-md hover:bg-slate-800 disabled:opacity-50"
                        onClick={handleGoogleSync}
                        disabled={syncLoading || !pickerSessionId}
                        type="button"
                      >
                        {syncLoading ? t('Queueing sync...') : t('Ingest selected photos')}
                      </button>
                      {syncMessage && <p className="text-green-600">{syncMessage}</p>}
                      {!pickerSessionId && (
                        <p className="text-slate-500">{t('Waiting for picker selection.')}</p>
                      )}
                    </div>

                    <div className="border-t border-slate-200 pt-3">
                      <div className="flex items-center justify-between">
                        <p className="text-sm text-slate-800 font-medium">
                          {t('Recently ingested from Google Photos')}
                        </p>
                        <button
                          className="text-xs text-slate-500 hover:text-slate-700 disabled:opacity-50"
                          onClick={() => loadRecentItems(true)}
                          disabled={recentLoading}
                          type="button"
                        >
                          {recentLoading ? t('Refreshing...') : t('Refresh')}
                        </button>
                      </div>
                      {recentError && <p className="text-xs text-red-600">{recentError}</p>}
                      {recentItems.length === 0 && !recentError ? (
                        <p className="text-xs text-slate-500">{t('No recent items yet.')}</p>
                      ) : (
                        <ul className="space-y-2 text-xs text-slate-600 mt-2">
                          {recentItems.map((item) => (
                            <li key={item.id} className="flex items-start gap-2">
                              <CheckCircle2 className="w-4 h-4 text-green-600 mt-0.5" />
                              {item.item_type === 'photo' && item.download_url ? (
                                <img
                                  src={item.download_url}
                                  alt={item.original_filename || t('Google Photos thumbnail')}
                                  className="w-10 h-10 rounded-md object-cover border border-slate-200"
                                  loading="lazy"
                                />
                              ) : item.item_type === 'video' && item.poster_url ? (
                                <div className="relative">
                                  <img
                                    src={item.poster_url}
                                    alt={item.original_filename || t('Video preview')}
                                    className="w-10 h-10 rounded-md object-cover border border-slate-200"
                                    loading="lazy"
                                  />
                                  <span className="absolute inset-0 flex items-center justify-center text-white">
                                    <span className="flex items-center justify-center w-5 h-5 rounded-full bg-black/60">
                                      <Play className="w-2.5 h-2.5" />
                                    </span>
                                  </span>
                                </div>
                              ) : item.item_type === 'video' ? (
                                <div className="w-10 h-10 rounded-md border border-slate-200 flex items-center justify-center text-slate-400">
                                  <Video className="w-5 h-5" />
                                </div>
                              ) : item.item_type === 'audio' ? (
                                <div className="w-10 h-10 rounded-md border border-slate-200 flex items-center justify-center text-slate-400">
                                  <Mic className="w-5 h-5" />
                                </div>
                              ) : (
                                <div className="w-10 h-10 rounded-md border border-slate-200 flex items-center justify-center text-slate-400">
                                  <FileText className="w-5 h-5" />
                                </div>
                              )}
                              <div>
                                <p className="text-slate-800 font-medium">
                                  {item.original_filename || item.storage_key}
                                </p>
                                <p className="text-slate-500">
                                  {formatDateTime(item.captured_at)}
                                </p>
                              </div>
                            </li>
                          ))}
                        </ul>
                      )}
                      {recentItems.length > 0 && (
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                          <span>
                            {t('Showing {count} of {total}', {
                              count: recentItems.length,
                              total: Math.max(recentTotal, recentItems.length),
                            })}
                          </span>
                          {hasMoreRecent && (
                            <button
                              type="button"
                              onClick={() => loadRecentItems(false)}
                              className="text-xs text-slate-500 hover:text-slate-700 disabled:opacity-60"
                              disabled={recentLoading}
                            >
                              {recentLoading ? t('Loading...') : t('Load more')}
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </PageMotion>
  );
};
