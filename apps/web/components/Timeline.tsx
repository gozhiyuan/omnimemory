import React, { useEffect, useMemo, useState } from 'react';
import { Calendar, Filter, Image as ImageIcon } from 'lucide-react';
import { apiGet } from '../services/api';
import { TimelineDay, TimelineItem } from '../types';

const formatDate = (value?: string) => {
  if (!value) return 'Unknown date';
  return new Date(value).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
};

const buildLabel = (item: TimelineItem) =>
  item.caption || item.original_filename || `${item.item_type} upload`;

export const Timeline: React.FC = () => {
  const [days, setDays] = useState<TimelineDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await apiGet<TimelineDay[]>('/timeline');
        if (mounted) {
          setDays(data);
        }
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : 'Failed to load timeline.');
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

  const hasItems = useMemo(() => days.some(day => day.items.length > 0), [days]);

  return (
    <div className="p-8 h-full overflow-y-auto">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-8 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Memory Timeline</h1>
          <p className="text-slate-500 mt-1">Your life, organized chronologically.</p>
        </div>
        <div className="flex space-x-2">
          <button className="flex items-center px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm text-slate-600 hover:bg-slate-50 shadow-sm">
            <Calendar className="w-4 h-4 mr-2" />
            Last 200 items
          </button>
          <button className="flex items-center px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm text-slate-600 hover:bg-slate-50 shadow-sm">
            <Filter className="w-4 h-4 mr-2" />
            Filter
          </button>
        </div>
      </div>

      {loading && (
        <div className="bg-white rounded-xl border border-slate-100 p-6 text-slate-500 shadow-sm">
          Loading timeline…
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-6">
          {error}
        </div>
      )}

      {!loading && !error && !hasItems && (
        <div className="bg-slate-50 rounded-xl border border-dashed border-slate-300 text-center py-16 text-slate-500">
          No memories yet. Upload something to start your timeline.
        </div>
      )}

      <div className="space-y-10">
        {days.map((day) => (
          <div key={day.date}>
            <div className="flex items-baseline justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-900">{formatDate(day.date)}</h2>
              <span className="text-xs text-slate-500">{day.item_count} items</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
              {day.items.map((item) => (
                <div
                  key={item.id}
                  className="group bg-white rounded-xl overflow-hidden shadow-sm hover:shadow-lg transition-all duration-300 border border-slate-100"
                >
                  <div className="relative aspect-[4/3] overflow-hidden bg-slate-100">
                    {item.download_url ? (
                      <img
                        src={item.download_url}
                        alt={buildLabel(item)}
                        className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-500"
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-slate-400">
                        <ImageIcon className="w-8 h-8" />
                      </div>
                    )}
                    <div className="absolute inset-0 bg-gradient-to-t from-black/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity flex flex-col justify-end p-4">
                      <p className="text-white text-sm font-medium truncate">{buildLabel(item)}</p>
                    </div>
                  </div>
                  <div className="p-4">
                    <div className="flex items-center text-xs text-slate-500 mb-2">
                      <Calendar className="w-3 h-3 mr-1" />
                      {formatDate(item.captured_at)}
                    </div>
                    <p className="text-sm text-slate-800 font-medium line-clamp-2 mb-3">
                      {buildLabel(item)}
                    </p>
                    <div
                      className={`text-xs w-fit px-2 py-1 rounded-full ${
                        item.processed
                          ? 'text-green-700 bg-green-50'
                          : 'text-slate-600 bg-slate-100'
                      }`}
                    >
                      {item.processed ? 'Processed' : 'Processing'}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
