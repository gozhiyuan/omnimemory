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

export type View = 'dashboard' | 'timeline' | 'chat' | 'upload' | 'settings';

export interface NavItem {
  id: View;
  label: string;
  icon: React.ReactNode;
}