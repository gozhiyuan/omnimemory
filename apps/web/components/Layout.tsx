import React, { useEffect, useMemo, useState } from 'react';
import { NavItem, View } from '../types';
import { LayoutDashboard, MessageSquare, Image, Upload, Settings, LogOut, Moon, Sun, UserCircle } from 'lucide-react';
import { Tooltip } from './Tooltip';
import { useSettings } from '../contexts/SettingsContext';
import { useAuth } from '../contexts/AuthContext';
import { useI18n } from '../i18n/useI18n';
import { apiPost } from '../services/api';

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

type DownloadUrlResponse = {
  key: string;
  url: string;
};

export const Layout: React.FC<LayoutProps> = ({ children, activeView, onChangeView }) => {
  const [theme, setTheme] = useState<ThemeMode>(getInitialTheme);
  const { settings } = useSettings();
  const { authEnabled, user: authUser, signOut } = useAuth();
  const { t } = useI18n();
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);

  const displayName =
    settings.profile.displayName || authUser?.name || authUser?.email || t('User');
  const displayInitial = useMemo(() => displayName.trim().charAt(0).toUpperCase() || 'U', [displayName]);
  const navItems = useMemo<NavItem[]>(
    () => [
      { id: 'timeline', label: t('Timeline'), icon: <Image size={20} /> },
      { id: 'chat', label: t('Assistant'), icon: <MessageSquare size={20} /> },
      { id: 'upload', label: t('Ingest'), icon: <Upload size={20} /> },
      { id: 'dashboard', label: t('Dashboard'), icon: <LayoutDashboard size={20} /> },
      { id: 'settings', label: t('Settings'), icon: <Settings size={20} /> },
    ],
    [t]
  );

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

  useEffect(() => {
    if (!settings.profile.photoKey) {
      setAvatarUrl(null);
      return;
    }
    let active = true;
    const resolvePhoto = async () => {
      try {
        const response = await apiPost<DownloadUrlResponse>('/storage/download-url', {
          key: settings.profile.photoKey,
        });
        if (active) {
          setAvatarUrl(response.url);
        }
      } catch {
        if (active) {
          setAvatarUrl(null);
        }
      }
    };
    void resolvePhoto();
    return () => {
      active = false;
    };
  }, [settings.profile.photoKey]);

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
          <p className="text-xs text-slate-500 uppercase tracking-wider font-semibold mb-4 pl-2">
            {t('Menu')}
          </p>
          <nav className="space-y-1">
            {navItems.map((item) => (
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
            <span className="text-[11px] uppercase tracking-wide text-slate-400">{t('Theme')}</span>
            <Tooltip
              label={theme === 'dark' ? t('Switch to light mode') : t('Switch to dark mode')}
              align="end"
            >
              <button
                type="button"
                onClick={() => setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))}
                aria-label={t('Toggle dark mode')}
                aria-pressed={theme === 'dark'}
                className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-900 text-slate-200 transition hover:text-white"
              >
                {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
              </button>
            </Tooltip>
          </div>
          <div className="flex items-center space-x-3 mb-4">
             {avatarUrl ? (
               <img 
                 src={avatarUrl}
                 alt={displayName}
                 className="w-8 h-8 rounded-full border border-slate-600 object-cover"
                 loading="lazy"
               />
             ) : (
               <div className="flex h-8 w-8 items-center justify-center rounded-full border border-slate-600 bg-slate-800 text-xs font-semibold text-white">
                 {displayInitial}
               </div>
             )}
             <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-white truncate">{displayName}</p>
                <p className="text-xs text-slate-500 truncate">
                  {settings.profile.language === 'zh' ? t('Chinese UI') : t('English UI')}
                </p>
             </div>
          </div>
          {authEnabled && (
            <button
              type="button"
              onClick={signOut}
              className="flex items-center space-x-2 text-xs text-slate-500 hover:text-white transition-colors"
            >
              <LogOut size={14} />
              <span>{t('Sign out')}</span>
            </button>
          )}
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 min-w-0 overflow-hidden bg-slate-50 relative dark:bg-slate-900">
        {children}
      </main>
    </div>
  );
};
