import React from 'react';
import { MOCK_MEMORIES } from '../constants';
import { MapPin, Calendar, Filter } from 'lucide-react';

export const Timeline: React.FC = () => {
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
            Oct 2023 - Dec 2023
          </button>
          <button className="flex items-center px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm text-slate-600 hover:bg-slate-50 shadow-sm">
            <Filter className="w-4 h-4 mr-2" />
            Filter
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
        {MOCK_MEMORIES.map((memory) => (
          <div key={memory.id} className="group bg-white rounded-xl overflow-hidden shadow-sm hover:shadow-lg transition-all duration-300 border border-slate-100">
            <div className="relative aspect-[4/3] overflow-hidden">
              <img 
                src={memory.src} 
                alt={memory.caption} 
                className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-500" 
              />
              <div className="absolute inset-0 bg-gradient-to-t from-black/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity flex flex-col justify-end p-4">
                 <p className="text-white text-sm font-medium truncate">{memory.caption}</p>
              </div>
            </div>
            <div className="p-4">
              <div className="flex items-center text-xs text-slate-500 mb-2">
                <Calendar className="w-3 h-3 mr-1" />
                {new Date(memory.date).toLocaleDateString()}
              </div>
              <p className="text-sm text-slate-800 font-medium line-clamp-2 mb-3">
                {memory.caption}
              </p>
              <div className="flex items-center text-xs text-primary-600 bg-primary-50 w-fit px-2 py-1 rounded-full">
                <MapPin className="w-3 h-3 mr-1" />
                {memory.location}
              </div>
            </div>
          </div>
        ))}
        {/* Placeholders to fill space */}
        {[1,2,3,4].map(i => (
           <div key={`p-${i}`} className="bg-slate-50 rounded-xl border border-dashed border-slate-300 flex items-center justify-center aspect-[4/3] text-slate-400">
             <span className="text-sm">More memories loading...</span>
           </div>
        ))}
      </div>
    </div>
  );
};
