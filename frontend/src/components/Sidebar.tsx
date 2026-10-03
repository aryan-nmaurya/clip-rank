import React from 'react';
import { Plus, Flame, Award, Video, Settings as SettingsIcon, Film, Activity, User, Home, Compass, Bot, BarChart3, Wand2 } from 'lucide-react';
import { AIStatus } from '../types';

interface SidebarProps {
  currentTab: 'home' | 'viral' | 'ranking' | 'discovery' | 'movie' | 'autopilot' | 'analytics' | 'autoshorts' | 'progress' | 'library' | 'settings';
  onSelectTab: (tab: 'home' | 'viral' | 'ranking' | 'discovery' | 'movie' | 'autopilot' | 'analytics' | 'autoshorts' | 'library' | 'settings') => void;
  aiStatus: AIStatus | null;
  onOpenDiagnostics: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentTab,
  onSelectTab,
  aiStatus,
  onOpenDiagnostics,
}) => {
  return (
    <aside className="w-64 h-screen bg-white border-r border-zinc-200/80 flex flex-col justify-between shrink-0 select-none">
      <div className="p-4 flex flex-col gap-6">
        {/* Brand */}
        <div className="flex items-center gap-2.5 px-2 pt-1 cursor-pointer" onClick={() => onSelectTab('home')}>
          <div className="w-8 h-8 rounded-xl bg-zinc-900 text-white flex items-center justify-center shadow-sm">
            <Film className="w-4 h-4 stroke-[2.2]" />
          </div>
          <div className="flex flex-col">
            <span className="font-semibold text-sm tracking-tight text-zinc-900">ClipRank</span>
            <span className="text-[10px] text-zinc-400 font-medium tracking-wide uppercase">Autonomous Studio</span>
          </div>
        </div>

        {/* Primary Action Button */}
        <button
          onClick={() => onSelectTab('home')}
          className="w-full h-10 px-4 rounded-xl bg-zinc-900 hover:bg-zinc-800 text-white font-medium text-sm flex items-center justify-center gap-2 transition-all shadow-sm active:scale-[0.98]"
        >
          <Plus className="w-4 h-4" />
          <span>New Creation</span>
        </button>

        {/* Navigation */}
        <nav className="flex flex-col gap-1">
          <button onClick={()=>onSelectTab('autoshorts')} className={`w-full h-10 px-3 rounded-lg flex items-center gap-3 text-sm font-semibold ${currentTab==='autoshorts'?'bg-fuchsia-950 text-white':'bg-fuchsia-50 text-fuchsia-950'}`}><Wand2 className="w-4 h-4"/>Auto Shorts</button>
          <button onClick={()=>onSelectTab('autopilot')} className={`w-full h-10 px-3 rounded-lg flex items-center gap-3 text-sm font-semibold ${currentTab==='autopilot'?'bg-cyan-950 text-white':'bg-cyan-50 text-cyan-950'}`}><Bot className="w-4 h-4"/>Autopilot</button>
          <button onClick={()=>onSelectTab('discovery')} className={`w-full h-9 px-3 rounded-lg flex items-center gap-3 text-sm font-medium ${currentTab==='discovery'?'bg-zinc-100':'text-zinc-600 hover:bg-zinc-50'}`}><Compass className="w-4 h-4 text-cyan-700"/>Viral Discovery</button>
          <button
            onClick={() => onSelectTab('home')}
            className={`w-full h-9 px-3 rounded-lg flex items-center gap-3 text-sm font-medium transition-colors ${
              currentTab === 'home'
                ? 'bg-zinc-100 text-zinc-900 font-semibold'
                : 'text-zinc-600 hover:text-zinc-900 hover:bg-zinc-50'
            }`}
          >
            <Home className="w-4 h-4 text-zinc-500" />
            <span>Create</span>
          </button>

          <button
            onClick={() => onSelectTab('viral')}
            className={`w-full h-9 px-3 rounded-lg flex items-center gap-3 text-sm font-medium transition-colors ${
              currentTab === 'viral'
                ? 'bg-zinc-100 text-zinc-900 font-semibold'
                : 'text-zinc-600 hover:text-zinc-900 hover:bg-zinc-50'
            }`}
          >
            <Flame className="w-4 h-4 text-rose-500" />
            <span>Viral Clips</span>
          </button>

          <button
            onClick={() => onSelectTab('ranking')}
            className={`w-full h-9 px-3 rounded-lg flex items-center gap-3 text-sm font-medium transition-colors ${
              currentTab === 'ranking'
                ? 'bg-zinc-100 text-zinc-900 font-semibold'
                : 'text-zinc-600 hover:text-zinc-900 hover:bg-zinc-50'
            }`}
          >
            <Award className="w-4 h-4 text-amber-500" />
            <span>Ranking Video</span>
          </button>

          <button onClick={()=>onSelectTab('movie')} className={`w-full h-9 px-3 rounded-lg flex items-center gap-3 text-sm font-medium ${currentTab==='movie'?'bg-violet-50 text-violet-950':'text-zinc-600 hover:bg-zinc-50'}`}><Film className="w-4 h-4 text-violet-600"/>Movie / Trailer</button>

          <button
            onClick={() => onSelectTab('library')}
            className={`w-full h-9 px-3 rounded-lg flex items-center gap-3 text-sm font-medium transition-colors ${
              currentTab === 'library'
                ? 'bg-zinc-100 text-zinc-900 font-semibold'
                : 'text-zinc-600 hover:text-zinc-900 hover:bg-zinc-50'
            }`}
          >
            <Video className="w-4 h-4 text-zinc-500" />
            <span>Library</span>
          </button>

          <button
            onClick={() => onSelectTab('analytics')}
            className={`w-full h-9 px-3 rounded-lg flex items-center gap-3 text-sm font-medium transition-colors ${
              currentTab === 'analytics'
                ? 'bg-zinc-100 text-zinc-900 font-semibold'
                : 'text-zinc-600 hover:text-zinc-900 hover:bg-zinc-50'
            }`}
          >
            <BarChart3 className="w-4 h-4 text-zinc-500" />
            <span>Analytics</span>
          </button>

          <button
            onClick={() => onSelectTab('settings')}
            className={`w-full h-9 px-3 rounded-lg flex items-center gap-3 text-sm font-medium transition-colors ${
              currentTab === 'settings'
                ? 'bg-zinc-100 text-zinc-900 font-semibold'
                : 'text-zinc-600 hover:text-zinc-900 hover:bg-zinc-50'
            }`}
          >
            <SettingsIcon className="w-4 h-4 text-zinc-500" />
            <span>Settings</span>
          </button>
        </nav>
      </div>

      {/* Bottom Part */}
      <div className="p-4 border-t border-zinc-100 flex flex-col gap-3">
        {/* AI Status Badge */}
        <button
          onClick={onOpenDiagnostics}
          className="w-full text-left p-2.5 rounded-xl bg-zinc-50 hover:bg-zinc-100 transition-colors border border-zinc-200/60 group cursor-pointer"
        >
          <div className="flex items-center justify-between mb-1">
            <span className="text-[11px] font-medium text-zinc-500 uppercase tracking-wider">AI Engine</span>
            <Activity className="w-3 h-3 text-zinc-400 group-hover:text-zinc-600" />
          </div>
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${aiStatus?.is_ready ? 'bg-emerald-500 animate-pulse' : 'bg-amber-500'}`} />
            <span className="text-xs font-medium text-zinc-800 truncate">
              {aiStatus ? aiStatus.status_text.replace('● ', '') : 'Checking status...'}
            </span>
          </div>
        </button>

        {/* Profile */}
        <div className="flex items-center gap-3 px-2 py-1">
          <div className="w-7 h-7 rounded-full bg-zinc-200 text-zinc-700 flex items-center justify-center font-medium text-xs">
            <User className="w-3.5 h-3.5" />
          </div>
          <div className="flex flex-col">
            <span className="text-xs font-semibold text-zinc-800">Local Studio</span>
            <span className="text-[10px] text-zinc-400">Real footage & AI</span>
          </div>
        </div>
      </div>
    </aside>
  );
};
