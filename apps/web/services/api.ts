import { translateFromStorage } from '../i18n/core';
import { clearStoredTokens, getBearerToken, isAuthEnabled } from './auth';
import { toast } from './toast';

const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

type ApiOptions = RequestInit & { json?: unknown };

const buildUrl = (path: string) => {
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }
  if (!path.startsWith('/')) {
    return `${API_BASE}/${path}`;
  }
  return `${API_BASE}${path}`;
};

const formatError = async (response: Response) => {
  const text = await response.text();
  return text || `${response.status} ${response.statusText}`;
};

export const apiRequest = async <T>(path: string, options: ApiOptions = {}): Promise<T> => {
  const { json, headers, ...rest } = options;
  const requestHeaders: HeadersInit = { ...headers };

  let body = rest.body;
  if (json !== undefined) {
    requestHeaders['Content-Type'] = 'application/json';
    body = JSON.stringify(json);
  }
  const bearerToken = await getBearerToken();
  if (bearerToken) {
    requestHeaders['Authorization'] = `Bearer ${bearerToken}`;
  }

  const notifyFailure = (message: string, status?: number) => {
    const title = status
      ? translateFromStorage('Request failed ({status})', { status })
      : translateFromStorage('Request failed');
    toast.error(title, message);
  };

  let response: Response;
  try {
    response = await fetch(buildUrl(path), { ...rest, headers: requestHeaders, body });
  } catch (err) {
    const message = err instanceof Error ? err.message : translateFromStorage('Network error');
    notifyFailure(message);
    throw err;
  }
  if (!response.ok) {
    if (response.status === 401 && isAuthEnabled()) {
      clearStoredTokens();
    }
    const message = await formatError(response);
    notifyFailure(message, response.status);
    throw new Error(message);
  }
  if (response.status === 204) {
    return null as T;
  }
  return response.json() as Promise<T>;
};

export const apiGet = async <T>(path: string): Promise<T> => apiRequest<T>(path);

export const apiPost = async <T>(path: string, json?: unknown): Promise<T> =>
  apiRequest<T>(path, { method: 'POST', json });

export const apiPostForm = async <T>(path: string, form: FormData): Promise<T> =>
  apiRequest<T>(path, { method: 'POST', body: form });

export const apiPatch = async <T>(path: string, json?: unknown): Promise<T> =>
  apiRequest<T>(path, { method: 'PATCH', json });

export const apiPut = async <T>(path: string, json?: unknown): Promise<T> =>
  apiRequest<T>(path, { method: 'PUT', json });

export const apiDelete = async <T>(path: string): Promise<T> =>
  apiRequest<T>(path, { method: 'DELETE' });
