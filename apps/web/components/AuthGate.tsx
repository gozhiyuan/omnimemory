import React from 'react';
import { LogIn } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { useI18n } from '../i18n/useI18n';
import { PageMotion } from './PageMotion';

export const AuthGate: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { authEnabled, loading, isAuthenticated, login, error } = useAuth();
  const { t } = useI18n();

  if (!authEnabled) {
    return <>{children}</>;
  }

  if (loading) {
    return (
      <PageMotion className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
        <div className="text-sm text-slate-500">{t('Signing you in...')}</div>
      </PageMotion>
    );
  }

  if (!isAuthenticated) {
    return (
      <PageMotion className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
        <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-600 text-white text-lg font-semibold">
              O
            </div>
            <div>
              <h1 className="text-lg font-semibold text-slate-900">{t('Welcome to OmniMemory')}</h1>
              <p className="text-sm text-slate-500">{t('Sign in to continue')}</p>
            </div>
          </div>
          {error && (
            <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {error}
            </div>
          )}
          <button
            type="button"
            onClick={login}
            className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800"
          >
            <LogIn className="h-4 w-4" />
            {t('Continue with Authentik')}
          </button>
          <p className="mt-3 text-xs text-slate-400">
            {t('You will be redirected to your Authentik workspace to sign in.')}
          </p>
        </div>
      </PageMotion>
    );
  }

  return <>{children}</>;
};
