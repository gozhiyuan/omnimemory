import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import {
  getAuthConfig,
  getStoredTokens,
  getUserProfile,
  handleAuthCallback,
  isAuthEnabled,
  logout,
  startLogin,
} from '../services/auth';
import { translateFromStorage } from '../i18n/core';

type AuthUser = {
  name: string | null;
  email: string | null;
};

type AuthContextValue = {
  authEnabled: boolean;
  loading: boolean;
  isAuthenticated: boolean;
  user: AuthUser | null;
  error: string | null;
  login: () => void;
  signOut: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const authEnabled = isAuthEnabled();
  const [loading, setLoading] = useState(authEnabled);
  const [isAuthenticated, setIsAuthenticated] = useState(!authEnabled);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const init = async () => {
      if (!authEnabled) {
        if (!active) return;
        setLoading(false);
        setIsAuthenticated(true);
        return;
      }
      const result = await handleAuthCallback();
      if (!active) return;
      if (result?.error) {
        setError(result.error);
      }
      const tokens = result?.tokens || getStoredTokens();
      if (tokens) {
        setUser(getUserProfile(tokens));
        setIsAuthenticated(true);
      } else {
        setIsAuthenticated(false);
      }
      setLoading(false);
    };
    void init();
    return () => {
      active = false;
    };
  }, [authEnabled]);

  useEffect(() => {
    if (!authEnabled) {
      return;
    }
    const handleAuthChange = () => {
      const tokens = getStoredTokens();
      if (tokens) {
        setUser(getUserProfile(tokens));
        setIsAuthenticated(true);
      } else {
        setUser(null);
        setIsAuthenticated(false);
      }
    };
    window.addEventListener('lifelog:auth-changed', handleAuthChange as EventListener);
    return () => {
      window.removeEventListener('lifelog:auth-changed', handleAuthChange as EventListener);
    };
  }, [authEnabled]);

  const login = useCallback(() => {
    setError(null);
    const config = getAuthConfig();
    if (!config.enabled) {
      setError(translateFromStorage('OIDC is not configured.'));
      return;
    }
    void startLogin();
  }, []);

  const signOut = useCallback(() => {
    logout();
  }, []);

  const value = useMemo(
    () => ({
      authEnabled,
      loading,
      isAuthenticated,
      user,
      error,
      login,
      signOut,
    }),
    [authEnabled, loading, isAuthenticated, user, error, login, signOut]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
};
