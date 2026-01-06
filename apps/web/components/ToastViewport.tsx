import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';
import { ToastPayload, ToastVariant } from '../services/toast';
import { translateFromStorage } from '../i18n/core';

type ToastRecord = {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
  duration: number;
};

const DEFAULT_DURATION = 4500;
const MAX_TOASTS = 4;

const createToastId = () => {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const VARIANT_STYLES: Record<ToastVariant, { container: string; icon: string; description: string }> = {
  info: {
    container: 'border-slate-200 bg-white text-slate-900',
    icon: 'text-blue-500',
    description: 'text-slate-600',
  },
  success: {
    container: 'border-emerald-200 bg-emerald-50 text-emerald-900',
    icon: 'text-emerald-600',
    description: 'text-emerald-800',
  },
  error: {
    container: 'border-rose-200 bg-rose-50 text-rose-900',
    icon: 'text-rose-600',
    description: 'text-rose-800',
  },
};

const VARIANT_ICON: Record<ToastVariant, React.ComponentType<{ className?: string }>> = {
  info: Info,
  success: CheckCircle2,
  error: AlertTriangle,
};

export const ToastViewport: React.FC = () => {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const timers = useRef<Map<string, number>>(new Map());

  const clearTimer = (id: string) => {
    const timer = timers.current.get(id);
    if (timer) {
      window.clearTimeout(timer);
      timers.current.delete(id);
    }
  };

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
    clearTimer(id);
  }, []);

  const addToast = useCallback(
    (payload: ToastPayload) => {
      if (!payload?.title) return;
      const id = createToastId();
      const toast: ToastRecord = {
        id,
        title: payload.title,
        description: payload.description,
        variant: payload.variant ?? 'info',
        duration: payload.duration ?? DEFAULT_DURATION,
      };

      setToasts((prev) => {
        const next = [...prev, toast];
        if (next.length > MAX_TOASTS) {
          const removed = next.shift();
          if (removed) {
            clearTimer(removed.id);
          }
        }
        return next;
      });

      if (toast.duration > 0) {
        const timer = window.setTimeout(() => removeToast(id), toast.duration);
        timers.current.set(id, timer);
      }
    },
    [removeToast]
  );

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<ToastPayload>).detail;
      if (!detail) return;
      addToast(detail);
    };
    window.addEventListener('lifelog:toast', handler as EventListener);
    return () => {
      window.removeEventListener('lifelog:toast', handler as EventListener);
      timers.current.forEach((timer) => window.clearTimeout(timer));
      timers.current.clear();
    };
  }, [addToast]);

  if (toasts.length === 0) {
    return null;
  }

  return (
    <div
      className="pointer-events-none fixed right-4 top-4 z-50 flex w-[360px] max-w-[90vw] flex-col gap-2"
      aria-live="polite"
      aria-atomic="true"
    >
      {toasts.map((toast) => {
        const styles = VARIANT_STYLES[toast.variant];
        const Icon = VARIANT_ICON[toast.variant];
        return (
          <div
            key={toast.id}
            className={`pointer-events-auto flex gap-3 rounded-lg border p-3 shadow-lg ${styles.container}`}
          >
            <div className="mt-0.5">
              <Icon className={`h-4 w-4 ${styles.icon}`} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">{toast.title}</div>
              {toast.description && (
                <div className={`mt-1 text-xs ${styles.description}`}>{toast.description}</div>
              )}
            </div>
            <button
              type="button"
              onClick={() => removeToast(toast.id)}
              className="text-slate-400 transition hover:text-slate-700"
              aria-label={translateFromStorage('Dismiss notification')}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        );
      })}
    </div>
  );
};
