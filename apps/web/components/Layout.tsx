import React from 'react';
import { NavItem, View } from '../types';
import { LayoutDashboard, MessageSquare, Image, Upload, Settings, LogOut } from 'lucide-react';

interface LayoutProps {
  children: React.ReactNode;
  activeView: View;
  onChangeView: (view: View) => void;
}

const NAV_ITEMS: NavItem[] = [
  { id: 'timeline', label: 'Timeline', icon: <Image size={20} /> },
  { id: 'chat', label: 'Assistant', icon: <MessageSquare size={20} /> },
  { id: 'upload', label: 'Ingest', icon: <Upload size={20} /> },
  { id: 'dashboard', label: 'Dashboard', icon: <LayoutDashboard size={20} /> },
  { id: 'settings', label: 'Settings', icon: <Settings size={20} /> },
];

export const Layout: React.FC<LayoutProps> = ({ children, activeView, onChangeView }) => {
  return (
    <div className="flex h-screen bg-white">
      {/* Sidebar */}
      <aside className="w-64 bg-slate-900 text-slate-300 flex flex-col flex-shrink-0">
        <div className="p-6">
          <div className="flex items-center space-x-2 text-white mb-6">
            <div className="w-8 h-8 bg-primary-600 rounded-lg flex items-center justify-center font-bold text-lg">
              O
            </div>
            <span className="text-xl font-bold tracking-tight">OmniMemory</span>
          </div>
          <p className="text-xs text-slate-500 uppercase tracking-wider font-semibold mb-4 pl-2">Menu</p>
          <nav className="space-y-1">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                onClick={() => onChangeView(item.id)}
                className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg transition-colors ${
                  activeView === item.id 
                    ? 'bg-primary-600 text-white shadow-md' 
                    : 'hover:bg-slate-800 hover:text-white'
                }`}
              >
                {item.icon}
                <span className="text-sm font-medium">{item.label}</span>
              </button>
            ))}
          </nav>
        </div>
        
        <div className="mt-auto p-6 border-t border-slate-800">
          <div className="flex items-center space-x-3 mb-4">
             <img 
               src="https://picsum.photos/seed/user/100/100" 
               alt="User" 
               className="w-8 h-8 rounded-full border border-slate-600"
             />
             <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-white truncate">Demo User</p>
                <p className="text-xs text-slate-500 truncate">demo@omnimemory.ai</p>
             </div>
          </div>
          <button className="flex items-center space-x-2 text-xs text-slate-500 hover:text-white transition-colors">
            <LogOut size={14} />
            <span>Sign out</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 min-w-0 overflow-hidden bg-slate-50 relative">
        {children}
      </main>
    </div>
  );
};
