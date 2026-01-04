import React from 'react';

export interface MemoryItem {
  id: string;
  type: 'image' | 'video' | 'text' | 'audio';
  src: string;
  thumbnail?: string;
  caption: string;
  date: string;
  location: string;
  processed: boolean;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  sources?: ChatSource[];
  attachments?: ChatAttachment[];
}

export interface ChatSource {
  context_id: string;
  source_item_id?: string;
  thumbnail_url?: string | null;
  timestamp?: string | null;
  snippet?: string | null;
  score?: number | null;
  title?: string | null;
}

export interface ChatAttachment {
  id: string;
  url: string;
  content_type?: string | null;
  created_at?: string | null;
}

export interface ChatResponse {
  message: string;
  session_id: string;
  sources: ChatSource[];
}

export interface AgentImageResponse {
  message: string;
  session_id: string;
  attachments: ChatAttachment[];
  prompt?: string | null;
  caption?: string | null;
}

export interface ChatSessionSummary {
  session_id: string;
  title?: string | null;
  created_at: string;
  last_message_at: string;
  message_count: number;
}

export interface ChatSessionDetail {
  session_id: string;
  title?: string | null;
  messages: Array<{
    id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    sources?: ChatSource[];
    attachments?: ChatAttachment[];
    created_at: string;
  }>;
}

export interface UserStats {
  totalMemories: number;
  storageUsedGB: number;
  thisWeekUploads: number;
  connectedSources: number;
}

export interface TimelineItem {
  id: string;
  item_type: 'photo' | 'video' | 'audio' | 'document';
  captured_at?: string;
  processed: boolean;
  processing_status?: string;
  storage_key: string;
  content_type?: string | null;
  original_filename?: string | null;
  caption?: string | null;
  download_url?: string | null;
  poster_url?: string | null;
}

export interface TimelineDay {
  date: string;
  item_count: number;
  items: TimelineItem[];
  episodes?: TimelineEpisode[];
  daily_summary?: TimelineDailySummary | null;
}

export interface TimelineEpisode {
  episode_id: string;
  title: string;
  summary: string;
  context_type: string;
  start_time_utc?: string | null;
  end_time_utc?: string | null;
  item_count: number;
  source_item_ids: string[];
  context_ids: string[];
  preview_url?: string | null;
}

export interface TimelineDailySummary {
  context_id: string;
  summary_date: string;
  title: string;
  summary: string;
  keywords: string[];
}

export interface TimelineContext {
  context_type: string;
  title: string;
  summary: string;
  keywords: string[];
  entities: Array<Record<string, unknown>>;
  location: Record<string, unknown>;
  processor_versions: Record<string, unknown>;
}

export interface TranscriptSegment {
  start_ms: number;
  end_ms: number;
  text: string;
  status?: string | null;
  error?: string | null;
}

export interface TimelineItemDetail extends TimelineItem {
  contexts: TimelineContext[];
  transcript_text?: string | null;
  transcript_segments?: TranscriptSegment[];
}

export type TimelineViewMode = 'day' | 'week' | 'month' | 'year' | 'all';

export interface TimelineFocus {
  viewMode?: TimelineViewMode;
  anchorDate?: string;
  itemId?: string;
  episodeContextId?: string;
}

export interface TimelineEpisodeDetail {
  episode_id: string;
  title: string;
  summary: string;
  context_type: string;
  start_time_utc?: string | null;
  end_time_utc?: string | null;
  source_item_ids: string[];
  contexts: TimelineContext[];
  items: TimelineItem[];
}

export interface DashboardActivityPoint {
  date: string;
  count: number;
}

export interface DashboardRecentItem {
  id: string;
  item_type: TimelineItem['item_type'];
  captured_at?: string;
  processed: boolean;
  storage_key: string;
  content_type?: string | null;
  original_filename?: string | null;
  caption?: string | null;
  download_url?: string | null;
  poster_url?: string | null;
}

export interface DashboardStatsResponse {
  total_items: number;
  processed_items: number;
  failed_items: number;
  active_connections: number;
  uploads_last_7_days: number;
  storage_used_bytes: number;
  recent_items: DashboardRecentItem[];
  activity: DashboardActivityPoint[];
  usage_this_week: UsageTotals;
  usage_all_time: UsageTotals;
  usage_daily: UsageDailyPoint[];
}

export interface UsageTotals {
  prompt_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface UsageDailyPoint {
  date: string;
  total_tokens: number;
  cost_usd: number;
}

export interface TimelineItemsResponse {
  items: TimelineItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface SearchResult {
  context_id: string;
  score?: number | null;
  context_type?: string | null;
  title?: string | null;
  summary?: string | null;
  event_time_utc?: string | null;
  source_item_ids: string[];
  payload?: Record<string, unknown> | null;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
}

export interface UploadUrlResponse {
  key: string;
  url: string;
  headers?: Record<string, string>;
}

export interface IngestResponse {
  item_id: string;
  task_id: string;
  status: string;
}

export interface GooglePhotosStatus {
  connected: boolean;
  connected_at?: string | null;
  expires_at?: string | null;
}

export interface GooglePhotosAuthUrlResponse {
  auth_url: string;
  state: string;
}

export interface GooglePhotosPickerSessionResponse {
  session_id: string;
  picker_uri: string;
}

export interface GooglePhotosPickerItem {
  id: string;
  base_url?: string | null;
  filename?: string | null;
  mime_type?: string | null;
  creation_time?: string | null;
}

export interface GooglePhotosPickerItemsResponse {
  items: GooglePhotosPickerItem[];
  status?: 'ready' | 'pending';
  message?: string | null;
}

export interface GooglePhotosSyncRequest {
  session_id?: string | null;
}

export interface GooglePhotosSyncResponse {
  task_id: string;
  status: string;
}

export type View = 'dashboard' | 'timeline' | 'chat' | 'upload' | 'settings';

export interface NavItem {
  id: View;
  label: string;
  icon: React.ReactNode;
}
