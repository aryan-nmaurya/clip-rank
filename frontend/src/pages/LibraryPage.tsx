import React, { useEffect, useState } from 'react';
import { Search, Film, ArrowRight, Trash2, Flame, Award } from 'lucide-react';
import { Project } from '../types';
import { listProjects, deleteProject } from '../api';

interface LibraryPageProps {
  onOpenProject: (projectId: string, jobId: string) => void;
}

export const LibraryPage: React.FC<LibraryPageProps> = ({ onOpenProject }) => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [search, setSearch] = useState('');
  const [modeFilter, setModeFilter] = useState<'all' | 'viral' | 'ranking'>('all');
  const [loading, setLoading] = useState(true);

  const fetchList = async () => {
    try {
      const data = await listProjects(modeFilter, search);
      setProjects(data);
    } catch {} finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchList();
  }, [search, modeFilter]);

  const handleDelete = async (e: React.MouseEvent, projectId: string) => {
    e.stopPropagation();
    if (!confirm('Delete this project?')) return;
    try {
      await deleteProject(projectId);
      setProjects((prev) => prev.filter((p) => p.id !== projectId));
    } catch {}
  };

  const formatTimeAgo = (dateStr: string) => {
    try {
      const diffSec = Math.floor((new Date().getTime() - new Date(dateStr).getTime()) / 1000);
      if (diffSec < 60) return 'just now';
      const diffMin = Math.floor(diffSec / 60);
      if (diffMin < 60) return `${diffMin}m ago`;
      const diffHour = Math.floor(diffMin / 60);
      if (diffHour < 24) return `${diffHour}h ago`;
      return `${Math.floor(diffHour / 24)}d ago`;
    } catch {
      return 'recently';
    }
  };

  return (
    <div className="flex-1 h-screen overflow-y-auto bg-[#FAF9F6] p-8 flex flex-col items-center">
      <div className="max-w-4xl w-full flex flex-col gap-6 pb-16">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-bold tracking-tight text-zinc-900">Project Library</h1>
          <p className="text-xs text-zinc-500">All your generated Viral Clips and Ranking Videos.</p>
        </div>

        {/* Filter Bar */}
        <div className="flex flex-col md:flex-row items-center justify-between gap-3">
          <div className="relative w-full md:w-80">
            <Search className="w-4 h-4 text-zinc-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search projects..."
              className="w-full h-10 pl-9 pr-4 rounded-xl bg-white border border-zinc-200/90 text-xs font-medium text-zinc-900 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/10 focus:border-zinc-400 shadow-2xs"
            />
          </div>

          <div className="flex items-center gap-1 p-1 rounded-xl bg-zinc-100 border border-zinc-200/60 self-start text-xs font-medium">
            {(['all', 'viral', 'ranking'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setModeFilter(tab)}
                className={`px-3 py-1.5 rounded-lg capitalize transition-colors ${
                  modeFilter === tab
                    ? 'bg-white text-zinc-900 font-semibold shadow-2xs'
                    : 'text-zinc-500 hover:text-zinc-900'
                }`}
              >
                {tab === 'all' ? 'All Projects' : tab === 'viral' ? 'Viral Clips' : 'Ranking Videos'}
              </button>
            ))}
          </div>
        </div>

        {/* Projects List */}
        {loading ? (
          <div className="py-12 text-center text-zinc-400 text-xs font-medium">
            Loading library...
          </div>
        ) : projects.length === 0 ? (
          <div className="py-16 bg-white rounded-2xl border border-zinc-200/80 p-8 flex flex-col items-center justify-center text-center gap-3">
            <Film className="w-8 h-8 text-zinc-300" />
            <span className="text-sm font-semibold text-zinc-800">No projects yet</span>
            <span className="text-xs text-zinc-400 max-w-sm">
              Create your first Viral Clips or Ranking Video to see them stored here.
            </span>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {projects.map((proj) => {
              const firstClip = proj.clips[0];
              const jobId = proj.job?.id || '';

              return (
                <div
                  key={proj.id}
                  onClick={() => onOpenProject(proj.id, jobId)}
                  className="bg-white rounded-2xl border border-zinc-200/80 hover:border-zinc-300 hover:shadow-sm p-4 flex items-center justify-between gap-4 cursor-pointer transition-all group"
                >
                  <div className="flex items-center gap-4 min-w-0">
                    {/* Thumbnail */}
                    <div className="w-14 h-20 rounded-xl bg-zinc-950 shrink-0 overflow-hidden flex items-center justify-center relative">
                      {firstClip?.preview_path ? (
                        <img
                          src={firstClip.preview_path}
                          alt={proj.title}
                          className="w-full h-full object-cover"
                        />
                      ) : proj.mode === 'viral' ? (
                        <Flame className="w-5 h-5 text-rose-500" />
                      ) : (
                        <Award className="w-5 h-5 text-amber-500" />
                      )}
                    </div>

                    <div className="flex flex-col gap-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-zinc-100 text-zinc-700">
                          {proj.mode === 'viral' ? 'Viral' : 'Ranking'}
                        </span>
                        <span className="text-xs text-zinc-400">·</span>
                        <span className="text-xs text-zinc-500 font-medium">
                          {proj.clips.length > 0 ? `${proj.clips.length} clip(s)` : proj.status}
                        </span>
                      </div>
                      <h4 className="text-sm font-semibold text-zinc-900 truncate group-hover:text-zinc-950">
                        {proj.title}
                      </h4>
                      <span className="text-xs text-zinc-400">
                        {formatTimeAgo(proj.created_at)}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={(e) => handleDelete(e, proj.id)}
                      title="Delete"
                      className="w-8 h-8 rounded-lg hover:bg-zinc-100 text-zinc-400 hover:text-red-600 flex items-center justify-center transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>

                    <button
                      onClick={() => onOpenProject(proj.id, jobId)}
                      className="h-8 px-3 rounded-xl bg-zinc-100 group-hover:bg-zinc-900 text-zinc-700 group-hover:text-white font-medium text-xs flex items-center gap-1.5 transition-colors"
                    >
                      <span>Open</span>
                      <ArrowRight className="w-3 h-3" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
