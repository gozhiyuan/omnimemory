import React, { useState } from 'react';
import { Layout } from './components/Layout';
import { Dashboard } from './components/Dashboard';
import { ChatInterface } from './components/ChatInterface';
import { Timeline } from './components/Timeline';
import { UploadManager } from './components/UploadManager';
import { TimelineFocus, View } from './types';

const getInitialView = (): View => {
  const params = new URLSearchParams(window.location.search);
  if (params.get('integration') === 'google_photos') {
    return 'upload';
  }
  return 'timeline';
};

const App: React.FC = () => {
  const [currentView, setCurrentView] = useState<View>(getInitialView);
  const [timelineFocus, setTimelineFocus] = useState<TimelineFocus | null>(null);

  const renderContent = () => {
    switch (currentView) {
      case 'dashboard':
        return (
          <Dashboard
            onOpenTimeline={(focus) => {
              setTimelineFocus(focus);
              setCurrentView('timeline');
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
        return (
          <div className="p-8 flex items-center justify-center h-full text-slate-400">
            <div className="text-center">
              <h2 className="text-xl font-semibold mb-2">Settings</h2>
              <p>Configuration options coming in Phase 2.</p>
            </div>
          </div>
        );
      default:
        return <Dashboard />;
    }
  };

  return (
    <Layout activeView={currentView} onChangeView={setCurrentView}>
      {renderContent()}
    </Layout>
  );
};

export default App;
