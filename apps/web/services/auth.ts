import { translateFromStorage } from '../i18n/core';

type StoredAuthTokens = {
  accessToken: string;
  idToken?: string;
  refreshToken?: string;
  expiresAt?: number;
};

type AuthConfig = {
  enabled: boolean;
  issuerUrl?: string;
  clientId?: string;
  redirectUri?: string;
  scopes: string;
  authorizationEndpoint?: string;
  tokenEndpoint?: string;
  logoutEndpoint?: string;
  postLogoutRedirectUri?: string;
};

type TokenResponse = {
  access_token: string;
  id_token?: string;
  refresh_token?: string;
  expires_in?: number;
};

const AUTH_STORAGE_KEY = 'lifelog.auth.tokens';
const PKCE_STATE_KEY = 'lifelog.auth.state';
const PKCE_VERIFIER_KEY = 'lifelog.auth.verifier';
const AUTH_RETURN_KEY = 'lifelog.auth.return_to';
const AUTH_EVENT = 'lifelog:auth-changed';

let refreshPromise: Promise<StoredAuthTokens | null> | null = null;

const base64UrlEncode = (input: ArrayBuffer | Uint8Array) => {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  let binary = '';
  bytes.forEach((b) => {
    binary += String.fromCharCode(b);
  });
  return btoa(binary).replace(/=+$/g, '').replace(/\+/g, '-').replace(/\//g, '_');
};

const generateRandomString = (length = 32) => {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
};

const sha256 = async (value: string) => {
  const encoder = new TextEncoder();
  const data = encoder.encode(value);
  return crypto.subtle.digest('SHA-256', data);
};

const normalizeIssuer = (issuer?: string) => {
  if (!issuer) return undefined;
  return issuer.endsWith('/') ? issuer : `${issuer}/`;
};

export const getAuthConfig = (): AuthConfig => {
  const issuerUrl = import.meta.env.VITE_OIDC_ISSUER_URL as string | undefined;
  const clientId = import.meta.env.VITE_OIDC_CLIENT_ID as string | undefined;
  const normalizedIssuer = normalizeIssuer(issuerUrl);
  const defaultRedirect = typeof window !== 'undefined' ? window.location.origin : undefined;
  const redirectUri = (import.meta.env.VITE_OIDC_REDIRECT_URI as string | undefined) || defaultRedirect;
  const scopes =
    (import.meta.env.VITE_OIDC_SCOPES as string | undefined) ||
    'openid profile email offline_access';
  const authorizationEndpoint =
    (import.meta.env.VITE_OIDC_AUTH_URL as string | undefined) ||
    (normalizedIssuer ? `${normalizedIssuer}authorize/` : undefined);
  const tokenEndpoint =
    (import.meta.env.VITE_OIDC_TOKEN_URL as string | undefined) ||
    (normalizedIssuer ? `${normalizedIssuer}token/` : undefined);
  const logoutEndpoint =
    (import.meta.env.VITE_OIDC_LOGOUT_URL as string | undefined) ||
    (normalizedIssuer ? `${normalizedIssuer}end-session/` : undefined);
  const postLogoutRedirectUri =
    (import.meta.env.VITE_OIDC_POST_LOGOUT_REDIRECT_URI as string | undefined) ||
    defaultRedirect;

  const enabled = Boolean(issuerUrl && clientId);
  return {
    enabled,
    issuerUrl,
    clientId,
    redirectUri,
    scopes,
    authorizationEndpoint,
    tokenEndpoint,
    logoutEndpoint,
    postLogoutRedirectUri,
  };
};

export const isAuthEnabled = () => getAuthConfig().enabled;

export const getStoredTokens = (): StoredAuthTokens | null => {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredAuthTokens) : null;
  } catch {
    return null;
  }
};

export const setStoredTokens = (tokens: StoredAuthTokens) => {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));
  window.dispatchEvent(new CustomEvent(AUTH_EVENT));
};

export const clearStoredTokens = () => {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.removeItem(AUTH_STORAGE_KEY);
  window.dispatchEvent(new CustomEvent(AUTH_EVENT));
};

const isTokenExpired = (tokens: StoredAuthTokens) => {
  if (!tokens.expiresAt) return false;
  const now = Date.now();
  return tokens.expiresAt - 30000 <= now;
};

const parseJwtPayload = (token?: string) => {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length < 2) return null;
  try {
    const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const decoded = atob(payload.padEnd(payload.length + ((4 - (payload.length % 4)) % 4), '='));
    return JSON.parse(decoded) as Record<string, unknown>;
  } catch {
    return null;
  }
};

export const getUserProfile = (tokens?: StoredAuthTokens | null) => {
  const payload = parseJwtPayload(tokens?.idToken || tokens?.accessToken);
  if (!payload) return null;
  return {
    name: (payload.name as string) || (payload.preferred_username as string) || null,
    email: (payload.email as string) || null,
  };
};

const fetchWithTimeout = async (
  input: RequestInfo | URL,
  init: RequestInit,
  timeoutMs: number
) => {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
};

const exchangeToken = async (body: URLSearchParams, tokenEndpoint?: string) => {
  if (!tokenEndpoint) {
    throw new Error('Missing token endpoint.');
  }
  const response = await fetchWithTimeout(
    tokenEndpoint,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    },
    8000
  );
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || 'Token exchange failed.');
  }
  const data = (await response.json()) as TokenResponse;
  return data;
};

export const refreshAccessToken = async (): Promise<StoredAuthTokens | null> => {
  const config = getAuthConfig();
  const tokens = getStoredTokens();
  if (!config.enabled || !tokens?.refreshToken) {
    return null;
  }
  if (refreshPromise) {
    return refreshPromise;
  }
  const body = new URLSearchParams({
    grant_type: 'refresh_token',
    refresh_token: tokens.refreshToken,
  });
  if (config.clientId) {
    body.append('client_id', config.clientId);
  }
  refreshPromise = exchangeToken(body, config.tokenEndpoint)
    .then((data) => {
      const expiresIn = data.expires_in ? Number(data.expires_in) : undefined;
      const next: StoredAuthTokens = {
        accessToken: data.access_token,
        idToken: data.id_token || tokens.idToken,
        refreshToken: data.refresh_token || tokens.refreshToken,
        expiresAt: expiresIn ? Date.now() + expiresIn * 1000 : tokens.expiresAt,
      };
      setStoredTokens(next);
      return next;
    })
    .catch(() => {
      clearStoredTokens();
      return null;
    })
    .finally(() => {
      refreshPromise = null;
    });
  return refreshPromise;
};

export const getAccessToken = async (): Promise<string | null> => {
  const config = getAuthConfig();
  if (!config.enabled) {
    return null;
  }
  const tokens = getStoredTokens();
  if (!tokens) {
    return null;
  }
  if (isTokenExpired(tokens)) {
    const refreshed = await refreshAccessToken();
    return refreshed?.accessToken || null;
  }
  return tokens.accessToken;
};

const hasSubjectOrEmail = (payload: Record<string, unknown> | null) => {
  if (!payload) return false;
  return Boolean(payload.sub || payload.email || payload.preferred_username);
};

export const getBearerToken = async (): Promise<string | null> => {
  const config = getAuthConfig();
  if (!config.enabled) {
    return null;
  }
  const tokens = getStoredTokens();
  if (!tokens) {
    return null;
  }

  const accessToken = await getAccessToken();
  const accessPayload = parseJwtPayload(accessToken ?? undefined);
  if (accessToken && hasSubjectOrEmail(accessPayload)) {
    return accessToken;
  }

  const idToken = tokens.idToken;
  const idPayload = parseJwtPayload(idToken);
  if (idToken && hasSubjectOrEmail(idPayload)) {
    return idToken;
  }

  return accessToken || idToken || null;
};

export const startLogin = async (): Promise<void> => {
  const config = getAuthConfig();
  if (!config.enabled || !config.authorizationEndpoint || !config.clientId || !config.redirectUri) {
    throw new Error('OIDC is not configured.');
  }
  const state = generateRandomString(16);
  const verifier = generateRandomString(48);
  const challenge = base64UrlEncode(await sha256(verifier));

  sessionStorage.setItem(PKCE_STATE_KEY, state);
  sessionStorage.setItem(PKCE_VERIFIER_KEY, verifier);
  sessionStorage.setItem(AUTH_RETURN_KEY, window.location.href);

  const params = new URLSearchParams({
    response_type: 'code',
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    scope: config.scopes,
    state,
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });

  window.location.href = `${config.authorizationEndpoint}?${params.toString()}`;
};

const clearAuthParams = () => {
  const url = new URL(window.location.href);
  ['code', 'state', 'session_state', 'iss', 'error', 'error_description'].forEach((param) =>
    url.searchParams.delete(param)
  );
  window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
};

export const handleAuthCallback = async () => {
  if (typeof window === 'undefined') {
    return { handled: false };
  }
  const url = new URL(window.location.href);
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  const error = url.searchParams.get('error');
  if (error) {
    clearAuthParams();
    return { handled: true, error: url.searchParams.get('error_description') || error };
  }
  if (!code || !state) {
    return { handled: false };
  }

  const expectedState = sessionStorage.getItem(PKCE_STATE_KEY);
  const verifier = sessionStorage.getItem(PKCE_VERIFIER_KEY);
  sessionStorage.removeItem(PKCE_STATE_KEY);
  sessionStorage.removeItem(PKCE_VERIFIER_KEY);

  if (!expectedState || !verifier || expectedState !== state) {
    clearAuthParams();
    return { handled: true, error: translateFromStorage('Invalid login state.') };
  }

  const config = getAuthConfig();
  if (!config.clientId || !config.redirectUri) {
    clearAuthParams();
    return { handled: true, error: translateFromStorage('OIDC client not configured.') };
  }

  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code,
    redirect_uri: config.redirectUri,
    code_verifier: verifier,
    client_id: config.clientId,
  });

  try {
    const data = await exchangeToken(body, config.tokenEndpoint);
    const expiresIn = data.expires_in ? Number(data.expires_in) : undefined;
    const tokens: StoredAuthTokens = {
      accessToken: data.access_token,
      idToken: data.id_token,
      refreshToken: data.refresh_token,
      expiresAt: expiresIn ? Date.now() + expiresIn * 1000 : undefined,
    };
    setStoredTokens(tokens);
    clearAuthParams();
    const returnTo = sessionStorage.getItem(AUTH_RETURN_KEY);
    sessionStorage.removeItem(AUTH_RETURN_KEY);
    if (returnTo) {
      window.history.replaceState({}, '', returnTo);
    }
    return { handled: true, tokens };
  } catch (err) {
    clearAuthParams();
    return {
      handled: true,
      error:
        err instanceof Error
          ? err.message
          : translateFromStorage('Token exchange failed.'),
    };
  }
};

export const logout = () => {
  const config = getAuthConfig();
  const tokens = getStoredTokens();
  clearStoredTokens();
  if (!config.enabled) {
    return;
  }
  if (config.logoutEndpoint && tokens?.idToken) {
    const params = new URLSearchParams({
      id_token_hint: tokens.idToken,
      post_logout_redirect_uri: config.postLogoutRedirectUri || window.location.origin,
    });
    window.location.href = `${config.logoutEndpoint}?${params.toString()}`;
  } else {
    window.location.reload();
  }
};
