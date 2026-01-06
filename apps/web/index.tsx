import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { AuthGate } from './components/AuthGate';
import { ToastViewport } from './components/ToastViewport';
import { AuthProvider } from './contexts/AuthContext';
import { SettingsProvider } from './contexts/SettingsContext';

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error("Could not find root element to mount to");
}

const root = ReactDOM.createRoot(rootElement);
root.render(
  <React.StrictMode>
    <AuthProvider>
      <ErrorBoundary>
        <SettingsProvider>
          <AuthGate>
            <App />
          </AuthGate>
        </SettingsProvider>
      </ErrorBoundary>
    </AuthProvider>
    <ToastViewport />
  </React.StrictMode>
);
