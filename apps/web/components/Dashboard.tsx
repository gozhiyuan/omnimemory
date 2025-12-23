import React from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { MOCK_STATS, ACTIVITY_DATA, MOCK_MEMORIES } from '../constants';
import { HardDrive, Image as ImageIcon, Link as LinkIcon, Activity, Calendar } from 'lucide-react';

const StatCard = ({ title, value, icon, subtext }: { title: string, value: string | number, icon: React.ReactNode, subtext?: string }) => (
  <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-100 flex items-start space-x-4 hover:shadow-md transition-shadow">
    <div className="p-3 bg-primary-50 text-primary-600 rounded-lg">
      {icon}
    </div>
    <div>
      <p className="text-sm font-medium text-slate-500">{title}</p>
      <h3 className="text-2xl font-bold text-slate-900 mt-1">{value}</h3>
      {subtext && <p className="text-xs text-slate-400 mt-1">{subtext}</p>}
    </div>
  </div>
);

const RecentActivityItem: React.FC<{ memory: typeof MOCK_MEMORIES[0] }> = ({ memory }) => (
  <div className="flex items-center space-x-4 p-3 hover:bg-slate-50 rounded-lg transition-colors cursor-pointer group">
    <img src={memory.src} alt={memory.caption} className="w-12 h-12 rounded-lg object-cover shadow-sm group-hover:scale-105 transition-transform" />
    <div className="flex-1 min-w-0">
      <p className="text-sm font-medium text-slate-900 truncate">{memory.caption}</p>
      <div className="flex items-center text-xs text-slate-500 mt-0.5">
        <Calendar className="w-3 h-3 mr-1" />
        <span>{new Date(memory.date).toLocaleDateString()}</span>
        <span className="mx-1">•</span>
        <span>{memory.location}</span>
      </div>
    </div>
    <div className="text-xs font-medium text-green-600 bg-green-50 px-2 py-1 rounded-full">
      Processed
    </div>
  </div>
);

export const Dashboard: React.FC = () => {
  return (
    <div className="p-8 space-y-8 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Dashboard</h1>
        <p className="text-slate-500 mt-1">Overview of your digital life log.</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard 
          title="Total Memories" 
          value={MOCK_STATS.totalMemories.toLocaleString()} 
          icon={<ImageIcon size={24} />} 
          subtext="+12 this week"
        />
        <StatCard 
          title="Storage Used" 
          value={`${MOCK_STATS.storageUsedGB} GB`} 
          icon={<HardDrive size={24} />} 
          subtext="15% of 25GB quota"
        />
        <StatCard 
          title="Weekly Uploads" 
          value={MOCK_STATS.thisWeekUploads} 
          icon={<Activity size={24} />} 
          subtext="Top 10% activity"
        />
        <StatCard 
          title="Connected Sources" 
          value={MOCK_STATS.connectedSources} 
          icon={<LinkIcon size={24} />} 
          subtext="Google Photos, Apple iCloud"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Activity Chart */}
        <div className="lg:col-span-2 bg-white p-6 rounded-xl shadow-sm border border-slate-100">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-semibold text-slate-900">Ingestion Activity</h2>
            <select className="text-sm border-slate-200 rounded-md text-slate-500 focus:ring-primary-500">
              <option>Last 7 Days</option>
              <option>Last 30 Days</option>
            </select>
          </div>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={ACTIVITY_DATA}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis 
                  dataKey="name" 
                  axisLine={false} 
                  tickLine={false} 
                  tick={{ fill: '#64748b', fontSize: 12 }} 
                  dy={10}
                />
                <YAxis 
                  axisLine={false} 
                  tickLine={false} 
                  tick={{ fill: '#64748b', fontSize: 12 }} 
                />
                <Tooltip 
                  cursor={{ fill: '#f8fafc' }}
                  contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                />
                <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]} barSize={32} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Recent Items */}
        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-100">
          <h2 className="text-lg font-semibold text-slate-900 mb-4">Recent Memories</h2>
          <div className="space-y-2">
            {MOCK_MEMORIES.slice(0, 5).map(memory => (
              <RecentActivityItem key={memory.id} memory={memory} />
            ))}
          </div>
          <button className="w-full mt-4 py-2 text-sm text-primary-600 font-medium hover:bg-primary-50 rounded-lg transition-colors">
            View All Memories
          </button>
        </div>
      </div>
    </div>
  );
};