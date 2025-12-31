import React, { useEffect, useMemo, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { HardDrive, Image as ImageIcon, Link as LinkIcon, Activity, Calendar, Mic, Play, Video } from 'lucide-react';
import { apiGet } from '../services/api';
import { DashboardRecentItem, DashboardStatsResponse } from '../types';

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

const formatDate = (value?: string) => {
  if (!value) return 'Unknown date';
  return new Date(value).toLocaleDateString();
};

const buildLabel = (item: DashboardRecentItem) =>
  item.caption || item.original_filename || `${item.item_type} upload`;

const RecentActivityItem: React.FC<{ item: DashboardRecentItem }> = ({ item }) => {
  const isVideo = item.item_type === 'video';
  const isAudio = item.item_type === 'audio';
  const thumbnailUrl = isVideo ? item.poster_url : item.download_url;
  return (
    <div className="flex items-center space-x-4 p-3 hover:bg-slate-50 rounded-lg transition-colors cursor-pointer group">
      {thumbnailUrl ? (
        <div className="relative w-12 h-12">
          <img
            src={thumbnailUrl}
            alt={buildLabel(item)}
            className="w-12 h-12 rounded-lg object-cover shadow-sm group-hover:scale-105 transition-transform"
          />
          {isVideo && (
            <span className="absolute inset-0 flex items-center justify-center text-white">
              <span className="flex items-center justify-center w-6 h-6 rounded-full bg-black/60">
                <Play className="w-3 h-3" />
              </span>
            </span>
          )}
        </div>
      ) : (
        <div className="w-12 h-12 rounded-lg bg-slate-100 flex items-center justify-center text-slate-400">
          {isVideo ? <Video className="w-5 h-5" /> : isAudio ? <Mic className="w-5 h-5" /> : <ImageIcon className="w-5 h-5" />}
        </div>
      )}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-slate-900 truncate">{buildLabel(item)}</p>
        <div className="flex items-center text-xs text-slate-500 mt-0.5">
          <Calendar className="w-3 h-3 mr-1" />
          <span>{formatDate(item.captured_at)}</span>
        </div>
      </div>
      <div
        className={`text-xs font-medium px-2 py-1 rounded-full ${
          item.processed ? 'text-green-600 bg-green-50' : 'text-slate-600 bg-slate-100'
        }`}
      >
        {item.processed ? 'Processed' : 'Processing'}
      </div>
    </div>
  );
};

export const Dashboard: React.FC = () => {
  const [stats, setStats] = useState<DashboardStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await apiGet<DashboardStatsResponse>('/dashboard/stats');
        if (mounted) {
          setStats(data);
        }
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : 'Failed to load dashboard.');
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, []);

  const storageUsedGB = useMemo(() => {
    if (!stats?.storage_used_bytes) return 0;
    return stats.storage_used_bytes / (1024 * 1024 * 1024);
  }, [stats?.storage_used_bytes]);

  const activityData = useMemo(
    () =>
      (stats?.activity || []).map(point => ({
        name: new Date(point.date).toLocaleDateString(undefined, { weekday: 'short' }),
        count: point.count,
      })),
    [stats?.activity]
  );

  return (
    <div className="p-8 space-y-8 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Dashboard</h1>
        <p className="text-slate-500 mt-1">Overview of your digital life log.</p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg">
          {error}
        </div>
      )}

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard 
          title="Total Memories" 
          value={stats ? stats.total_items.toLocaleString() : '—'}
          icon={<ImageIcon size={24} />} 
          subtext={stats ? `${stats.processed_items} processed` : undefined}
        />
        <StatCard 
          title="Storage Used" 
          value={stats ? `${storageUsedGB.toFixed(2)} GB` : '—'} 
          icon={<HardDrive size={24} />} 
          subtext={stats ? `${stats.failed_items} failed items` : undefined}
        />
        <StatCard 
          title="Weekly Uploads" 
          value={stats ? stats.uploads_last_7_days : '—'} 
          icon={<Activity size={24} />} 
          subtext="Last 7 days"
        />
        <StatCard 
          title="Connected Sources" 
          value={stats ? stats.active_connections : '—'} 
          icon={<LinkIcon size={24} />} 
          subtext="Active connections"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Activity Chart */}
        <div className="lg:col-span-2 bg-white p-6 rounded-xl shadow-sm border border-slate-100">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-semibold text-slate-900">Ingestion Activity</h2>
            <select className="text-sm border-slate-200 rounded-md text-slate-500 focus:ring-primary-500">
              <option>Last 7 Days</option>
            </select>
          </div>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={activityData}>
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
            {stats?.recent_items?.length ? (
              stats.recent_items.map(item => (
                <RecentActivityItem key={item.id} item={item} />
              ))
            ) : (
              <div className="text-sm text-slate-500 py-4 text-center">
                {loading ? 'Loading recent memories…' : 'No recent memories yet.'}
              </div>
            )}
          </div>
          <button className="w-full mt-4 py-2 text-sm text-primary-600 font-medium hover:bg-primary-50 rounded-lg transition-colors">
            View All Memories
          </button>
        </div>
      </div>
    </div>
  );
};
