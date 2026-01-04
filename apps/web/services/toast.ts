export type ToastVariant = 'info' | 'success' | 'error';

export type ToastPayload = {
  title: string;
  description?: string;
  variant?: ToastVariant;
  duration?: number;
};

const emitToast = (payload: ToastPayload) => {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<ToastPayload>('lifelog:toast', { detail: payload }));
};

const buildPayload = (
  title: string,
  description?: string,
  options?: Omit<ToastPayload, 'title' | 'description'>
): ToastPayload => ({
  title,
  description,
  ...options,
});

export const toast = {
  info: (title: string, description?: string, options?: Omit<ToastPayload, 'title' | 'description'>) =>
    emitToast(buildPayload(title, description, { variant: 'info', ...options })),
  success: (title: string, description?: string, options?: Omit<ToastPayload, 'title' | 'description'>) =>
    emitToast(buildPayload(title, description, { variant: 'success', ...options })),
  error: (title: string, description?: string, options?: Omit<ToastPayload, 'title' | 'description'>) =>
    emitToast(buildPayload(title, description, { variant: 'error', ...options })),
};

export { emitToast };
