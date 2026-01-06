import React, { useCallback, useEffect, useState } from 'react';
import { Layout } from './components/Layout';
import { Dashboard } from './components/Dashboard';
import { ChatInterface } from './components/ChatInterface';
import { Timeline } from './components/Timeline';
import { UploadManager } from './components/UploadManager';
import { Settings } from './components/Settings';
import { TimelineFocus, View } from './types';

const VALID_VIEWS: View[] = ['dashboard', 'chat', 'timeline', 'upload', 'settings'];

const resolveViewParam = (value: string | null): View | null => {
  if (!value) {
    return null;
  }
  return VALID_VIEWS.includes(value as View) ? (value as View) : null;
};

const getViewFromLocation = (): View => {
  const params = new URLSearchParams(window.location.search);
  if (params.get('integration') === 'google_photos') {
    return 'upload';
  }
  const viewParam = resolveViewParam(params.get('view'));
  return viewParam ?? 'timeline';
};

const buildViewUrl = (view: View): string => {
  const url = new URL(window.location.href);
  if (url.searchParams.get('integration') === 'google_photos') {
    return `${url.pathname}${url.search}`;
  }
  url.searchParams.set('view', view);
  const search = url.searchParams.toString();
  return `${url.pathname}${search ? `?${search}` : ''}`;
};

const App: React.FC = () => {
  const [currentView, setCurrentView] = useState<View>(getViewFromLocation);
  const [timelineFocus, setTimelineFocus] = useState<TimelineFocus | null>(null);

  const navigate = useCallback(
    (nextView: View, options?: { replace?: boolean }) => {
      const shouldReplace = options?.replace ?? nextView === currentView;
      setCurrentView(nextView);
      const nextUrl = buildViewUrl(nextView);
      if (shouldReplace) {
        window.history.replaceState({ view: nextView }, '', nextUrl);
      } else {
        window.history.pushState({ view: nextView }, '', nextUrl);
      }
    },
    [currentView]
  );

  useEffect(() => {
    const handleFocus = (event: Event) => {
      const detail = (event as CustomEvent).detail as TimelineFocus | undefined;
      if (!detail) {
        return;
      }
      setTimelineFocus(detail);
      navigate('timeline');
    };
    window.addEventListener('lifelog:timeline-focus', handleFocus as EventListener);
    return () => {
      window.removeEventListener('lifelog:timeline-focus', handleFocus as EventListener);
    };
  }, [navigate]);

  useEffect(() => {
    const handlePopState = (event: PopStateEvent) => {
      const stateView = resolveViewParam(event.state?.view);
      const nextView = stateView ?? getViewFromLocation();
      setCurrentView(nextView);
    };
    window.addEventListener('popstate', handlePopState);
    window.history.replaceState({ view: currentView }, '', buildViewUrl(currentView));
    return () => {
      window.removeEventListener('popstate', handlePopState);
    };
  }, []);

  const renderContent = () => {
    switch (currentView) {
      case 'dashboard':
        return (
          <Dashboard
            onOpenTimeline={(focus) => {
              setTimelineFocus(focus);
              navigate('timeline');
            }}
          />
        );
      case 'chat':
        return <ChatInterface />;
      case 'timeline':
        return (
          <Timeline
            focus={timelineFocus}
            onFocusHandled={() => setTimelineFocus(null)}
          />
        );
      case 'upload':
        return <UploadManager />;
      case 'settings':
        return <Settings />;
      default:
        return <Dashboard />;
    }
  };

  return (
    <Layout activeView={currentView} onChangeView={(view) => navigate(view)}>
      {renderContent()}
    </Layout>
  );
};

export default App;
