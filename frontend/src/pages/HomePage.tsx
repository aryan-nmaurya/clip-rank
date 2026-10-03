import React from 'react';
import { Flame, Award, ArrowRight, Sparkles, Video, Play } from 'lucide-react';

interface HomePageProps {
  onSelectFlow: (flow: 'viral' | 'ranking' | 'discovery' | 'movie' | 'autopilot') => void;
}

export const HomePage: React.FC<HomePageProps> = ({ onSelectFlow }) => {
  return (
    <div className="flex-1 h-screen overflow-y-auto flex flex-col items-center p-8 bg-[#FAF9F6]">
      <div className="max-w-3xl w-full flex flex-col gap-10 pb-12">
        {/* Header */}
        <div className="flex flex-col gap-2 text-center items-center">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-zinc-100 text-zinc-700 text-xs font-semibold uppercase tracking-wider mb-2">
            <Sparkles className="w-3.5 h-3.5 text-zinc-900" />
            <span>ClipRank</span>
          </div>
          <h1 className="text-4xl font-extrabold tracking-tight text-zinc-900">
            What do you want to create?
          </h1>
          <p className="text-zinc-500 text-sm max-w-md">
            Find surprising visual moments, rank verified payoffs, or turn your own footage into finished Shorts.
          </p>
        </div>

        <button onClick={()=>onSelectFlow('autopilot')} className="rounded-3xl bg-cyan-950 text-white text-left p-7"><h2 className="text-xl font-bold">Run your content studio</h2><p className="text-sm text-cyan-100 mt-2">Extreme, unbelievable & funny moments · Discovery, original commentary, copyright checks and publishing.</p><span className="inline-block mt-4 font-semibold text-sm">Open Autopilot →</span></button>
        <button onClick={()=>onSelectFlow('discovery')} className="text-left rounded-2xl border bg-white p-6"><h2 className="font-bold text-lg">Viral Discovery</h2><p className="text-sm text-zinc-500 mt-1">Discover what to create. Discover extraordinary footage and produce original viral clips, rankings or commentary.</p></button>
        <button onClick={()=>onSelectFlow('movie')} className="rounded-2xl border border-violet-200 bg-white p-6 text-left flex items-center gap-5 hover:border-violet-500 transition-colors"><div className="rounded-2xl bg-violet-50 p-4 text-violet-700"><Video size={26}/></div><div className="flex-1"><h2 className="font-bold text-lg">Movie / Trailer Moments</h2><p className="text-sm text-zinc-500 mt-1">Turn cinematic scenes into engaging Shorts automatically.</p><span className="text-xs font-semibold text-violet-800 inline-block mt-3">Create Movie Clips →</span></div></button>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Workflow A: Viral Clips */}
          <div
            onClick={() => onSelectFlow('viral')}
            className="group relative bg-white rounded-3xl border border-zinc-200/90 hover:border-zinc-900/40 p-8 shadow-sm hover:shadow-xl transition-all duration-300 cursor-pointer flex flex-col justify-between gap-6 overflow-hidden active:scale-[0.99]"
          >
            <div className="flex flex-col gap-4">
              <div className="w-14 h-14 rounded-2xl bg-rose-50 text-rose-600 flex items-center justify-center group-hover:scale-110 group-hover:bg-rose-600 group-hover:text-white transition-all duration-300 shadow-sm">
                <Flame className="w-7 h-7" />
              </div>
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-bold text-rose-600 uppercase tracking-wider">Workflow A</span>
                <h3 className="text-xl font-bold text-zinc-900 group-hover:text-zinc-950">Viral Clips</h3>
                <p className="text-zinc-500 text-xs leading-relaxed">
                  Upload a video or paste a public link. Select complete highlights, preserve the footage and audio, and add captions timed to the actual speech.
                </p>
              </div>
            </div>

            <div className="flex items-center justify-between pt-4 border-t border-zinc-100">
              <span className="text-xs font-semibold text-zinc-800 group-hover:text-zinc-950">
                Input video → Multiple viral clips
              </span>
              <div className="w-8 h-8 rounded-full bg-zinc-100 group-hover:bg-zinc-900 group-hover:text-white text-zinc-700 flex items-center justify-center transition-colors">
                <ArrowRight className="w-4 h-4" />
              </div>
            </div>
          </div>

          {/* Workflow B: Ranking Video */}
          <div
            onClick={() => onSelectFlow('ranking')}
            className="group relative bg-white rounded-3xl border border-zinc-200/90 hover:border-zinc-900/40 p-8 shadow-sm hover:shadow-xl transition-all duration-300 cursor-pointer flex flex-col justify-between gap-6 overflow-hidden active:scale-[0.99]"
          >
            <div className="flex flex-col gap-4">
              <div className="w-14 h-14 rounded-2xl bg-amber-50 text-amber-600 flex items-center justify-center group-hover:scale-110 group-hover:bg-amber-600 group-hover:text-white transition-all duration-300 shadow-sm">
                <Award className="w-7 h-7" />
              </div>
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-bold text-amber-600 uppercase tracking-wider">Workflow B</span>
                <h3 className="text-xl font-bold text-zinc-900 group-hover:text-zinc-950">Ranking Video</h3>
                <p className="text-zinc-500 text-xs leading-relaxed">
                  Find individual clips across sites, screen out existing rankings, and produce two distinct countdown Shorts with natural commentary, synchronized captions, and finished audio.
                </p>
              </div>
            </div>

            <div className="flex items-center justify-between pt-4 border-t border-zinc-100">
              <span className="text-xs font-semibold text-zinc-800 group-hover:text-zinc-950">
                Enter topic → Two finished #5 → #1 Shorts
              </span>
              <div className="w-8 h-8 rounded-full bg-zinc-100 group-hover:bg-zinc-900 group-hover:text-white text-zinc-700 flex items-center justify-center transition-colors">
                <ArrowRight className="w-4 h-4" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
