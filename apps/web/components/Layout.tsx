import React, { useEffect, useState } from 'react';
import { NavItem, View } from '../types';
import { LayoutDashboard, MessageSquare, Image, Upload, Settings, LogOut, Moon, Sun } from 'lucide-react';
import { Tooltip } from './Tooltip';

type ThemeMode = 'light' | 'dark';

const THEME_KEY = 'lifelog.theme';

const getInitialTheme = (): ThemeMode => {
  if (typeof window === 'undefined') {
    return 'light';
  }
  const stored = window.localStorage.getItem(THEME_KEY);
  if (stored === 'light' || stored === 'dark') {
    return stored;
  }
  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
  return prefersDark ? 'dark' : 'light';
};

interface LayoutProps {
  children: React.ReactNode;
  activeView: View;
  onChangeView: (view: View) => void;
}

const NAV_ITEMS: NavItem[] = [
  { id: 'timeline', label: 'Timeline', icon: <Image size={20} /> },
  { id: 'chat', label: 'Assistant', icon: <MessageSquare size={20} /> },
  { id: 'upload', label: 'Ingest', icon: <Upload size={20} /> },
  { id: 'dashboard', label: 'Dashboard', icon: <LayoutDashboard size={20} /> },
  { id: 'settings', label: 'Settings', icon: <Settings size={20} /> },
];

export const Layout: React.FC<LayoutProps> = ({ children, activeView, onChangeView }) => {
  const [theme, setTheme] = useState<ThemeMode>(getInitialTheme);

  useEffect(() => {
    if (typeof document === 'undefined') {
      return;
    }
    document.documentElement.classList.toggle('dark', theme === 'dark');
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch {
      // Ignore localStorage failures.
    }
  }, [theme]);

  return (
    <div className="flex h-screen bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      {/* Sidebar */}
      <aside className="w-64 bg-slate-900 text-slate-300 flex flex-col flex-shrink-0 dark:bg-slate-950">
        <div className="p-6">
          <div className="flex items-center space-x-2 text-white mb-6">
            <div className="w-8 h-8 bg-primary-600 rounded-lg flex items-center justify-center font-bold text-lg">
              O
            </div>
            <span className="text-xl font-bold tracking-tight">OmniMemory</span>
          </div>
          <p className="text-xs text-slate-500 uppercase tracking-wider font-semibold mb-4 pl-2">Menu</p>
          <nav className="space-y-1">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                onClick={() => onChangeView(item.id)}
                className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg transition-colors ${
                  activeView === item.id 
                    ? 'bg-primary-600 text-white shadow-md' 
                    : 'hover:bg-slate-800 hover:text-white'
                }`}
              >
                {item.icon}
                <span className="text-sm font-medium">{item.label}</span>
              </button>
            ))}
          </nav>
        </div>
        
        <div className="mt-auto p-6 border-t border-slate-800 dark:border-slate-800">
          <div className="mb-4 flex items-center justify-between rounded-lg border border-slate-800 bg-slate-800/60 px-3 py-2">
            <span className="text-[11px] uppercase tracking-wide text-slate-400">Theme</span>
            <Tooltip label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'} align="end">
              <button
                type="button"
                onClick={() => setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))}
                aria-label="Toggle dark mode"
                aria-pressed={theme === 'dark'}
                className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-900 text-slate-200 transition hover:text-white"
              >
                {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
              </button>
            </Tooltip>
          </div>
          <div className="flex items-center space-x-3 mb-4">
             <img 
               src="https://picsum.photos/seed/user/100/100" 
               alt="User" 
               className="w-8 h-8 rounded-full border border-slate-600"
               loading="lazy"
             />
             <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-white truncate">Demo User</p>
                <p className="text-xs text-slate-500 truncate">demo@omnimemory.ai</p>
             </div>
          </div>
          <button className="flex items-center space-x-2 text-xs text-slate-500 hover:text-white transition-colors">
            <LogOut size={14} />
            <span>Sign out</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 min-w-0 overflow-hidden bg-slate-50 relative dark:bg-slate-900">
        {children}
      </main>
    </div>
  );
};
