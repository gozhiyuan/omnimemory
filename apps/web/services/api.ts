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

  const response = await fetch(buildUrl(path), { ...rest, headers: requestHeaders, body });
  if (!response.ok) {
    throw new Error(await formatError(response));
  }
  if (response.status === 204) {
    return null as T;
  }
  return response.json() as Promise<T>;
};

export const apiGet = async <T>(path: string): Promise<T> => apiRequest<T>(path);

export const apiPost = async <T>(path: string, json?: unknown): Promise<T> =>
  apiRequest<T>(path, { method: 'POST', json });

export const apiDelete = async <T>(path: string): Promise<T> =>
  apiRequest<T>(path, { method: 'DELETE' });
