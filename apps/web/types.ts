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
  role: 'user' | 'model';
  content: string;
  timestamp: Date;
  sources?: MemoryItem[];
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
  storage_key: string;
  content_type?: string | null;
  original_filename?: string | null;
  caption?: string | null;
  download_url?: string | null;
}

export interface TimelineDay {
  date: string;
  item_count: number;
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

export type View = 'dashboard' | 'timeline' | 'chat' | 'upload' | 'settings';

export interface NavItem {
  id: View;
  label: string;
  icon: React.ReactNode;
}
